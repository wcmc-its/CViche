"""Section H: honors and awards (#398).

The template table is three columns -- award name, organization, year -- and
almost none of the work here is filling it. Source CVs write honors as one free
line ("2020 AECT Distinguished Service Award, Purdue University"), so the
section has to take that line apart before it can be rendered, and that parsing
is what this module mostly is:

    _split_award_year               peels a leading or trailing year off the
                                    line, including ranges and the dangling
                                    month "..., August 2025." leaves behind
    _extract_organization_from_award finds the granting body by institutional
                                    keyword, in several passes
    _add_honors_row                 the only part that touches the table

`_US_STATE_ABBREVS` and `_MONTH_TAIL_RE` are the two vocabularies those parsers
filter against, and they exist because of specific mis-parses (#229): a bare
state abbreviation out of "Bethesda, MD" is not an organization, and a trailing
month is not part of an award's name. Both stay class attributes so the moved
bodies keep reading them as `self.<name>`.

Three of these -- `_fill_honors`, `_extract_organization_from_award` and
`_split_award_year` -- are on the pinned class surface in
`tests/test_stage6_import_surface.py`; the mixin keeps them resolving through
the MRO, which is what that guard checks.
"""
import re
from typing import Dict, List, Sequence, Tuple

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..normalization import _strip_org_tail
from ..parsing import _is_table_header_entry
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


# A '|' part that is nothing but a year or a year range is a date column, not
# an award. `_fill_honors` classifies parts with exactly this shape below, and
# `_entry_parts` has to agree with it, so the pattern is compiled once here and
# shared rather than written out twice (docs/CODING_STANDARDS.md §8.2).
_YEAR_ONLY_RE = re.compile(
    r'^(\d{4}(?:\s*-\s*\d{4})?|\d{4}(?:\s*-\s*present)?)$', re.IGNORECASE)

# Below this many award-looking parts the entry is not a multi-award list.
_MIN_AWARDS_FOR_SPLIT = 2


def _entry_parts(text: str, column_values: Sequence[str] = ()) -> List[str]:
    """Non-empty parts of an honors entry (#476), scoped to exactly the
    newline-blind case entry_lines already names as its own blind spot: a
    text with no literal newline at all, where entry_lines returns the whole
    thing as a single opaque part.

    A genuinely multi-line entry (entry_lines already returns >1 part) is
    returned UNCHANGED -- its existing per-part tab/pipe handling below stays
    exactly as today. Reading the farm's changed uids proved this matters:
    2054_Opresko_Cv's "1994 | American Chemical Society Award,\\nLehigh Valley
    Chapter of ACS" already has 2 lines by newline alone, and additionally
    splitting line 0's '|' collides with the existing (separate, pre-existing)
    "DATE | AWARD" mislabeling in the loop below, adding a spurious duplicate
    row that isn't this issue's to fix.

    For the single-line, tab-free case, '|' is added as a boundary -- the
    corpus's other unambiguous "Award Name | Year" separator (module
    docstring, existing `'|' in line` branch) -- but a line carrying a tab is
    still returned whole, untouched by '|' too. 293 of the farm's
    newline-blind H entries carry a tab, and reading them shows why: 038WKA's
    "Award Name\\tDate\\tDescription" is one award with three tab-separated
    fields, correctly rejoined today by the per-part loop's "does the last
    tab part look like a year" check; 2082_Dr_Scot's is a mid-sentence
    line-wrap artifact ("...NISOD) Award for\\tTeaching Excellence.
    Valencia..." ) that pre-splitting tears in half, verified to produce three
    garbled rows instead of the one correct one `award_name` already
    carries. And 2068_Yount_Cv's single blind entry has BOTH a tab and a '|'
    ("2022\\tWorld's Best ... Ranking | Research.com"): splitting on '|' first
    hands the tab-rejoin branch a part with no leading year to isolate,
    which is what makes it emit a spurious "Research.com" row instead of
    correctly falling back to the clean extracted fields. Excluding any
    tab-bearing line from the '|' split avoids all three at once by leaving
    every one of them exactly as `entry_lines` already had it.

    Splitting on '|' unconditionally was wrong in one direction the farm
    cannot show, and this is the guard for it: the pipeline's own table-cell
    join writes ONE award as "Award | Organization | Year", and splitting that
    re-emitted the organization as a second, empty award row (a row `dev` does
    not produce). `column_values` are the column cells stage 4 already
    extracted for this entry -- its granting body and its date -- and a part
    they already name is a column, not an award. What survives that filter,
    plus the bare-year parts, decides:

    - fewer than `_MIN_AWARDS_FOR_SPLIT` award-looking parts: not a list of
      awards, return `lines` unchanged (`dev`'s exact behaviour);
    - bare years present but not one per award: the pipes are column
      separators, not record separators -- "Award | Organization | Year" has
      two award-looking parts and one year, so it fails here even when stage 4
      extracted nothing to filter with. Return `lines` unchanged.
    - otherwise (each award carries its own year, or none of them do): the
      pipes separate records, so the split stands.

    Note what is returned on the split path: `parts`, the WHOLE split, never
    the filtered `awards` list -- the filters decide, they never drop text.

    Residual, disclosed rather than guessed at: a two-column "Award |
    Organization" join for which stage 4 extracted no granting body has two
    award-looking parts and no year, and still splits. Nothing in the farm has
    that shape and inventing a third rule for it would be untested.
    """
    lines = entry_lines(text)
    if len(lines) != 1 or '\t' in lines[0]:
        return lines
    parts = [p.strip() for p in lines[0].split('|') if p.strip()]
    claimed = {v.strip().lower() for v in column_values if v and v.strip()}
    years = [p for p in parts if _YEAR_ONLY_RE.match(p)]
    awards = [p for p in parts
              if not _YEAR_ONLY_RE.match(p) and p.lower() not in claimed]
    if len(awards) < _MIN_AWARDS_FOR_SPLIT:
        return lines
    if years and len(years) != len(awards):
        return lines
    return parts


class HonorsSection:
    """Section H writers, mixed into `WCMTemplateGenerator`."""

    # "MD" (from "Bethesda, MD") and "Bloomington" are comma segments the
    # short-proper-noun org fallback happily returns (#229) — never treat a
    # bare state abbreviation as an organization.
    _US_STATE_ABBREVS = frozenset({
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI',
        'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI',
        'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC',
        'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT',
        'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'})

    _MONTH_TAIL_RE = re.compile(
        r'[\s,]*(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)$', re.IGNORECASE)

    def _split_award_year(self, text: str) -> Tuple[str, str]:
        """Split an award line into (name-without-year, year-or-range).

        Handles the shapes the honors fallback parser actually sees (#229):
        leading years/ranges ("2020 AECT ...", "2015-2017 Featured ...") and
        trailing years with punctuation ("..., August 2025." / "... (2021)").
        Returns the original text and '' when no year is found.
        """
        m = re.match(r'^\s*((?:19|20)\d{2}(?:\s*[-–]\s*'
                     r'(?:(?:19|20)\d{2}|present))?)\b[\s,.:–-]*',
                     text, re.IGNORECASE)
        if m:
            return text[m.end():].strip(' ,.;'), m.group(1)
        m = re.search(r'(?:^|[\s,(])((?:19|20)\d{2})\s*[).]?\s*$', text)
        if m:
            cleaned = text[:m.start()].rstrip(' ,.(;')
            # "..., August 2025." leaves a dangling month — drop it too
            cleaned = self._MONTH_TAIL_RE.sub('', cleaned).rstrip(' ,.;')
            return cleaned, m.group(1)
        return text, ''

    def _fill_honors(self, entries: List[Dict]):
        """Fill H. HONORS, AWARDS section.

        WCM template has table with columns: Name of award | Organization | Date awarded (yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Honors ({len(entries)} entries)...")

        # Find the HONORS section
        honors_idx = self._find_paragraph_with_text("HONORS")
        if honors_idx is None:
            honors_idx = self._find_paragraph_with_text("AWARDS")
        if honors_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(honors_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries that were mistakenly extracted as data
            # Common patterns: "Name of award\tOrganization\tDate awarded" or similar
            if _is_table_header_entry(original_text, ['award', 'honor', 'organization', 'date', 'year', 'granting']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Get fields for table columns
            award_name = fields.get('award_name', '')
            granting_body = fields.get('granting_body', '') or fields.get('organization', '')
            date = fields.get('date', '') or fields.get('year', '')

            # Check if this entry contains multiple awards (newline-separated)
            # This happens when multiple honors were merged during extraction
            # #476: '\n' and '|' boundaries; the extracted column cells are
            # passed so a single award's own "Award | Organization | Year"
            # cell join is not mistaken for two awards -- see _entry_parts.
            lines = _entry_parts(original_text, (granting_body, date))

            # Separate award lines from year lines
            # Years are typically 4-digit numbers or ranges like "2017-2020"
            year_pattern = _YEAR_ONLY_RE

            # Header patterns to skip (tab-separated column headers from source CV tables)
            header_keywords = {'name of award', 'date awarded', 'organization', 'granting body', 'honor', 'year'}

            award_lines = []
            year_lines = []
            for line in lines:
                # Skip lines that look like table column headers
                # e.g., "Name of award\tDate awarded" or "Name of award\tOrganization\tDate awarded"
                line_lower = line.lower().replace('\t', ' ')
                if sum(1 for kw in header_keywords if kw in line_lower) >= 2:
                    continue

                # Handle tab-separated "Award Name\tYear" format
                if '\t' in line:
                    tab_parts = [p.strip() for p in line.split('\t') if p.strip()]
                    if len(tab_parts) >= 2 and year_pattern.match(tab_parts[-1]):
                        # Last tab-field is a year, everything before is the award
                        award_lines.append('\t'.join(tab_parts[:-1]))
                        year_lines.append(tab_parts[-1])
                        continue
                    elif len(tab_parts) == 1:
                        # Tab-prefixed year or award
                        line = tab_parts[0]
                    # else: treat as normal line with tabs stripped
                    else:
                        line = ' '.join(tab_parts)

                # Check if line is just a year
                if year_pattern.match(line):
                    year_lines.append(line)
                # Check for "Award Name | Year" format
                elif '|' in line:
                    parts = line.split('|')
                    award_lines.append(parts[0].strip())
                    if len(parts) > 1 and parts[1].strip():
                        year_lines.append(parts[1].strip())
                else:
                    award_lines.append(line)

            # If we have multiple awards in the text, process each separately
            if len(award_lines) > 1:
                # Determine year ordering: if years are in descending order (most recent first,
                # matching typical reverse-chronological award lists), use forward mapping.
                # If ascending, reverse them to align with descending awards.
                def _extract_first_year(y):
                    m = re.match(r'(\d{4})', y)
                    return int(m.group(1)) if m else 0

                if len(year_lines) >= 2:
                    first_y = _extract_first_year(year_lines[0])
                    last_y = _extract_first_year(year_lines[-1])
                    ordered_years = year_lines if first_y >= last_y else list(reversed(year_lines))
                else:
                    ordered_years = year_lines

                for i, award_text in enumerate(award_lines):
                    # Stage 4 extracted clean fields for (at most) one award of
                    # the fused entry — use them for the line they belong to
                    # instead of re-parsing it from raw text (#229).
                    if award_name and award_name.lower() in award_text.lower():
                        self._add_honors_row(
                            table, award_name,
                            granting_body or self._extract_organization_from_award(award_text),
                            format_date_for_section(date, 'H') if date else '')
                        continue

                    # Try to get corresponding year from ordered list
                    year_for_award = ''
                    if i < len(ordered_years):
                        year_for_award = ordered_years[i]

                    # If no year found from text, extract the inline year
                    # (leading "2020 Award ...", range, or trailing "... 2025.")
                    if not year_for_award:
                        award_text, year_for_award = self._split_award_year(award_text)

                    # Format date
                    if year_for_award:
                        year_for_award = format_date_for_section(year_for_award, 'H')

                    # Extract organization from award text
                    org = self._extract_organization_from_award(award_text)

                    # The org is usually a trailing segment of the raw line —
                    # keep it out of the name cell (#229)
                    award_text = _strip_org_tail(award_text, org)

                    # Add row
                    self._add_honors_row(table, award_text, org, year_for_award)
            else:
                # Single award - use extracted fields
                if not award_name:
                    award_name = original_text[:150]

                # If no extracted date, parse the inline year out of the name
                # (leading "2021 Award ...", range, or trailing "... 2021.");
                # fall back to the original text for the year alone.
                if not date:
                    award_name, date = self._split_award_year(award_name)
                    if not date:
                        _, date = self._split_award_year(original_text)

                # Format date as yyyy
                if date:
                    date = format_date_for_section(date, 'H')

                # Extract organization if field extraction didn't provide one
                if not granting_body:
                    granting_body = self._extract_organization_from_award(award_name)

                # Same duplication hazard as the multi-award path (#229)
                award_name = _strip_org_tail(award_name, granting_body)

                self._add_honors_row(table, award_name, granting_body, date)

    def _extract_organization_from_award(self, text: str) -> str:
        """Extract organization name from award/honor text using institutional keyword patterns.

        Uses a multi-strategy approach:
        1. 'from [Organization]' explicit pattern
        2. Comma-separated segments with institutional keywords
        3. 'Association/Society of X' at start of text
        4. Proper noun phrases around institutional keywords anywhere in text

        Returns the organization name, or empty string if none identified.
        """
        if not text:
            return ''

        # Words that are part of award descriptions, not organization names
        STOP = frozenset(['award', 'excellence', 'teaching', 'list', 'recognition',
                          'member', 'elected', 'senior', 'certificate', 'mentoring',
                          'director', 'subinternship', 'housestaff', 'faculty',
                          'resident', 'scholarship', 'honor', 'clinical', 'student'])

        IKW = (r'(?:University|College|Hospital|Medical\s+Center|Society|Association|'
               r'Institute|Academy|Foundation|Program\s+Directors|Center)')

        def _build_org_around_keyword(txt):
            """Find last institutional keyword in text and build org name around it."""
            keywords = list(re.finditer(IKW, txt, re.IGNORECASE))
            if not keywords:
                return ''
            km = keywords[-1]  # Use last keyword to capture full org span

            # Walk backwards from keyword
            before = txt[:km.start()]
            words = before.rstrip().split()
            pre = []
            for w in reversed(words):
                wc = w.strip('.,;\u2013\u2014-()\"\u2019')
                if not wc:
                    # Dash/punctuation-only token - preserve and keep walking
                    pre.insert(0, w.strip())
                    continue
                if wc.lower() in STOP:
                    break
                if wc[0].islower() and wc.lower() not in ('of', 'the', 'and', 'at', 'in', 'for'):
                    break
                pre.insert(0, wc)

            # Walk forward: handle dash-connected institution names
            after = txt[km.end():]
            post = ''
            dm = re.match(r'(\s*[\u2013\u2014-]\s*(?:[A-Z][\w.]+\s+)*?' + IKW + r')', after)
            if dm:
                post = dm.group(1).strip('\u2013\u2014- ').strip()

            parts = pre + [km.group(0)]
            org = ' '.join(parts)
            if post:
                org += ' \u2013 ' + post
            return org.strip('.,; ')

        # Strategy 1: "from [Organization]"
        fm = re.search(r'\bfrom\b\s+(.+)$', text, re.IGNORECASE)
        if fm:
            org = _build_org_around_keyword(fm.group(1))
            if len(org.split()) >= 2:
                return org

        # Strategy 2: comma-separated segments (check last segments first).
        # Two passes: an institutional-keyword segment anywhere beats the
        # short-proper-noun fallback — a single reversed pass used to return
        # "MD" or a bare city before ever reaching the real org (#229).
        if ',' in text:
            segs = [s.strip().rstrip('.,;') for s in text.split(',')]
            for seg in reversed(segs):
                if seg and re.search(IKW, seg, re.IGNORECASE) and len(seg.split()) <= 10:
                    return seg
            for seg in reversed(segs):
                if not seg or seg.upper() in self._US_STATE_ABBREVS \
                        or any(ch.isdigit() for ch in seg):
                    continue
                # Short proper-noun segment (e.g., "Weill Cornell")
                if re.match(r'^[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,2}$', seg):
                    return seg

        # Strategy 3: "Association/Society of X" at start of text
        m = re.match(
            r'((?:Medical\s+)?' + IKW + r'\s+(?:of|for)\s+(?:the\s+)?(?:State\s+of\s+)?'
            r'[A-Z][\w\s.-]+?)(?:\s+(?:Mentoring|Award|Certificate|Medical\s+Student|Grant))',
            text, re.IGNORECASE
        )
        if m:
            return m.group(1).strip().rstrip('.,;')

        # Strategy 4: institutional keyword anywhere - build org around last match
        org = _build_org_around_keyword(text)
        if len(org.split()) >= 2 and len(org) < len(text) * 0.7:
            return org

        return ''

    def _add_honors_row(self, table, award_name: str, granting_body: str, date: str):
        """Add a single row to the honors table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = award_name or ''
            row.cells[1].text = granting_body or ''
            row.cells[2].text = date or ''
        elif num_cols >= 2:
            row.cells[0].text = award_name or ''
            row.cells[1].text = date or ''
        else:
            row.cells[0].text = f"{award_name} ({date})" if date else award_name

        # Apply font formatting to each cell
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        self.stats['entries_inserted'] += 1
