"""Section R: invitations to speak and present (#398).

Three columns -- title, institution/location, year -- across three sibling
tables: Regional, National, International. The routing between them is the whole
section. Scope is not a field on the entry; it is inferred from where the talk
was given relative to where the CV owner works, which is why the writer reads
`cv_owner_location` and delegates to `_classify_geographic_scope` (shared, so it
stays on `WCMTemplateGenerator`).

Locating the three tables is done by walking forward from the section heading
and matching a paragraph that IS the scope word, allowing a trailing asterisk
("National*" carries a template footnote). A substring match would hit the
heading text itself. The window is capped at 25 paragraphs so a template missing
one subsection cannot capture a table belonging to the section below.

A template with no scope subsections at all degrades to one National table, and
an entry whose scope has no table falls back to National as well -- an
unclassifiable talk is published in the middle bucket rather than dropped.

Dates arrive under four different field names and field extraction emits the
STRING "None" often enough that it is checked for explicitly at both the `year`
and the `start_date` fallback; a literal "None" in the year column is worse than
a blank one. A title-less entry is titled by `_untitled_talk_title`: role and
event name when the source line holds nothing else, otherwise the source line
without the list number, year and venue the other two cells already show.
"""
import logging
import re

from ..fan_out import fallback_text
from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

# The pieces of an R source line the row already shows elsewhere, stripped when
# stage 4 found no talk title (EBYSBC E23: RNKYST-06, MRJDWE-08, GJXIWD-04).
_LIST_NUMBER_RE = re.compile(r'^\s*(?:\(\d{1,3}\)|\d{1,3}[.)])\s+')
_LEADING_YEAR_RE = re.compile(r'^\s*((?:19|20)\d{2})(?!\d)[\s.,:;\-–—]*')
_YEAR_RE = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')
_MONTH = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?'
# A date closing the line ("..., March 3-4, 1997."); group 1 is its year.
_WEEKDAY = r'(?:mon|tues|wednes|thurs|fri|satur|sun)day'
_TRAILING_DATE_RE = re.compile(
    rf'[\s,;:\-–—(]*(?:{_WEEKDAY},?\s*)?(?:{_MONTH}\s*)?(?:\d{{1,2}}(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?,?\s*)?'
    r'((?:19|20)\d{2})\)?\.?\s*$', re.IGNORECASE)
_WORD_RE = re.compile(r'[^\W\d_]{2,}')
_DATE_WORD_RE = re.compile(rf'\b{_MONTH}(?!\w)|\d+(?:st|nd|rd|th)?\b', re.IGNORECASE)
# Joining words left over once role, event and venue are taken out of a source
# line: "Invited Speaker at the <event>" says nothing more than its parts.
_CONNECTIVES = frozenset({'at', 'the', 'of', 'and', 'in', 'on', 'for', 'to', 'an', 'by', 'with'})
_TITLE_EDGES = ' \t,;:|-–—'
_SEPARATOR_RUN_RE = re.compile(r'\s*([,;:|\-–—])(?:\s*[,;:|.\-–—])+\s*')


def _normalize_space(text: str) -> str:
    """Collapse whitespace runs (source lines are often tab-aligned) and fold
    curly apostrophes so field values match the line they were taken from."""
    return re.sub(r'\s+', ' ', text.replace('\u2019', "'").replace('\u2018', "'")).strip()


def _remove_phrase(text: str, phrase: str) -> str:
    """`text` without each whole-word, case-insensitive occurrence of `phrase`."""
    if not phrase:
        return text
    return re.sub(rf'(?<!\w){re.escape(phrase)}(?!\w)', ' ', text, flags=re.IGNORECASE)


def _remove_venue(text: str, venue: str) -> str:
    """`text` without `venue` where it stands as a segment of its own, set off
    by separators or the line's edges. Inside a phrase ("a conference hosted
    by <venue>") it stays, or the phrase would be left cut short."""
    if not venue:
        return text
    return re.sub(
        rf'(?:^|(?<=[,;:|\-–—(]))\s*{re.escape(venue)}(?=\s*(?:[,;:|.\-–—()]|$|\d|{_MONTH}(?!\w)))',
        ' ', text, flags=re.IGNORECASE)


def _untitled_talk_title(text: str, role: str, event_name: str, venue: str,
                         date_cell: str) -> str:
    """The Title cell of an R entry stage 4 found no title for.

    The source line restates what the Institution and Dates cells already
    show. Strip its list number, the year the Dates cell shows when it opens
    or closes the line, and the venue. When nothing is left beyond role,
    event name and date words, the title is role + event name ("Visiting
    Professor, <event>"); anything more may be the talk title stage 4 missed
    (#475), so the stripped line renders instead.
    """
    shown_years = set(_YEAR_RE.findall(date_cell or ''))
    line = _LIST_NUMBER_RE.sub('', _normalize_space(text or ''), count=1)
    leading_year = _LEADING_YEAR_RE.match(line)
    if leading_year and leading_year.group(1) in shown_years:
        line = line[leading_year.end():]
    line = _remove_venue(line, _normalize_space(venue or ''))
    trailing_date = _TRAILING_DATE_RE.search(line)
    if trailing_date and trailing_date.group(1) in shown_years:
        line = line[:trailing_date.start()]

    residue = _remove_phrase(line, _normalize_space(venue or ''))
    for phrase in sorted((_normalize_space(role or ''), _normalize_space(event_name or '')),
                         key=len, reverse=True):
        residue = _remove_phrase(residue, phrase)
    residue = _DATE_WORD_RE.sub(' ', residue)
    if not any(w.casefold() not in _CONNECTIVES for w in _WORD_RE.findall(residue)):
        # The stage-4 values as given, so the venue's own duplicate check
        # in `_fill_presentations` recognises them in the title.
        return ', '.join(part.strip() for part in (role or '', event_name or '') if part.strip())
    return _SEPARATOR_RUN_RE.sub(r'\1 ', _normalize_space(line)).strip(_TITLE_EDGES)


class PresentationsSection:
    """Section R writers, mixed into `WCMTemplateGenerator`."""

    def _fill_presentations(self, entries: list[dict]):
        """Fill R. INVITATIONS TO SPEAK/PRESENT section.

        WCM template has tables for Regional/National/International:
        Table structure: Title | Institution/Location | Dates (yyyy)

        Uses cv_owner_location to classify geographic scope of each entry.
        """
        if not entries:
            return

        if self.verbose:
            logger.info("Filling Invited Presentations (%s entries)...", len(entries))

        # Find Presentations section
        section_idx = self._find_header_paragraph("INVITATIONS TO SPEAK")
        if section_idx is None:
            section_idx = self._find_header_paragraph("Invited Presentations")
        if section_idx is None:
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            # Look for scope header (may have * suffix like "National*")
            scope_idx = None
            for i in range(section_idx, min(section_idx + 25, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope or para_text == f"{scope}*":
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(section_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            return

        # Clear tables and mark as populated
        for scope, table in tables_by_scope.items():
            _clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

        # Classify and route entries by geographic scope
        entries_by_scope = {'Regional': [], 'National': [], 'International': []}
        for entry in entries:
            scope = self._classify_geographic_scope(entry)
            entries_by_scope[scope].append(entry)

        if self.verbose and self.cv_owner_location:
            regional_count = len(entries_by_scope['Regional'])
            national_count = len(entries_by_scope['National'])
            intl_count = len(entries_by_scope['International'])
            logger.info("  Presentations: %s Regional, %s National, %s International", regional_count, national_count, intl_count)

        # Fill each table with its entries
        for scope, scope_entries in entries_by_scope.items():
            if not scope_entries:
                continue

            # Find the table for this scope (fall back to National)
            table = tables_by_scope.get(scope) or tables_by_scope.get('National')
            if not table:
                continue

            sorted_entries = sort_entries_reverse_chronological(scope_entries)

            for entry in sorted_entries:
                fields = entry.get('extracted_fields', {}) or {}
                taxonomy_code = entry.get('taxonomy_code', 'R')

                title = fields.get('title') or fields.get('presentation_title') or ''
                institution = fields.get('institution') or fields.get('location') or fields.get('venue') or ''

                # Get date, falling back to start_date/end_date for multi-date entries
                raw_date = fields.get('year') or fields.get('date') or ''
                # Handle the string "None" from field extraction
                if str(raw_date).strip().lower() == 'none':
                    raw_date = ''
                if not raw_date:
                    # Fall back to start_date for entries with date ranges
                    raw_date = fields.get('start_date') or ''
                    if str(raw_date).strip().lower() == 'none':
                        raw_date = ''
                # Format date as yyyy per WCM template requirements
                formatted_date = format_date_for_section(raw_date, 'R') if raw_date else ''

                role = str(fields.get('role') or '').strip()
                if role.lower() == 'none':
                    role = ''

                if not title:
                    # A split record's own line, not every record's (#1445).
                    title = _untitled_talk_title(
                        fallback_text(entry), role,
                        str(fields.get('event_name') or ''), institution, formatted_date)

                # The R block is Title | Institution/Location | Dates, so the
                # meeting that hosted the talk has no column of its own and
                # belongs with the venue: "ASMBS 2022 Presidential Grand Rounds,
                # Dallas, TX". event_name was read nowhere in this file, so
                # 2,194 of 2,956 extracted values across 79 CVs reached no part
                # of the rendered document.
                event_name = (fields.get('event_name') or '').strip()
                if event_name and event_name.casefold() not in f"{institution} {title}".casefold():
                    institution = f"{event_name}, {institution}" if institution else event_name

                # The speaker's role ("Visiting Professor") likewise has no
                # column; stage 4 extracts it as `role` and it leads the venue.
                if role and role.casefold() not in f"{institution} {title}".casefold():
                    institution = f"{role}, {institution}" if institution else role

                row = table.add_row()
                num_cols = len(row.cells)
                if num_cols >= 3:
                    row.cells[0].text = title or ''
                    row.cells[1].text = institution or ''
                    row.cells[2].text = formatted_date
                elif num_cols >= 2:
                    row.cells[0].text = title or ''
                    row.cells[1].text = formatted_date

                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            _set_font(run)
                self.stats['entries_inserted'] += 1
