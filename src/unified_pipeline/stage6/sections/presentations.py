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
STRING "None" often enough that it is checked for explicitly on every one; a
literal "None" in the year column is worse than a blank one. An `end_date`
makes the Dates cell a range, and further dates under an off-schema span key
follow it (`_talk_date_cell`, #1245). Its dates are `formatting`'s
`presentation_date_spans`, which the #1205 sub-point pass counts. A title-less entry is titled by `_untitled_talk_title`: role and
event name when the source line holds nothing else, otherwise the source line
without the list number, year and venue the other two cells already show.

Every row written is recorded in the row-receipt ledger against the entry it
renders (#534), so the #1205 sub-point pass anchors a talk's prose only under
that entry's own row. The fields each cell is read from are
`supplementary`'s, which that pass counts as the row's.
"""
import logging
import re
from collections.abc import Mapping
from itertools import pairwise

from ..fan_out import fallback_text
from ..formatting import (
    DATE_SPAN_SEPARATOR,
    PRESENTATION_DATE_FIELDS,
    _set_font,
    presentation_date_spans,
    with_event_status,
)
from ..sorting import sort_entries_reverse_chronological
from ..supplementary import (
    PRESENTATION_EVENT_FIELDS,
    PRESENTATION_TITLE_FIELDS,
    PRESENTATION_VENUE_FIELDS,
)

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
# A numeric date opening the line ("12/15/2025", "10/27/04", "03/2019"); group 1
# is its year, two digits when the source wrote it short (PZTNWT 118-164,
# VPMMFM 524, #1346).
_LEADING_NUMERIC_DATE_RE = re.compile(
    r'^\s*\d{1,2}/(?:\d{1,2}/)?(\d{4}|\d{2})[\s.,:;\-–—]*')
_SHORT_YEAR_DIGITS = 2
# The pieces of a role, event or venue value, each of which the row already
# shows: a source line gives "<institution><TAB><city>" for the venue stage 4
# wrote as "<institution>, <city>", and "Roundtable, panelist, <chapter>" for
# the event "Roundtable, <chapter>" (#1346).
_VALUE_PIECE_SPLIT_RE = re.compile(r'\s*[,;()]\s*')
# Words naming the kind of appearance, not its subject. A line left with only
# these beyond role, event and venue ("<date> <event><TAB><venue>. Invited
# talk.", 17 rows of PZTNWT) is titled by role and event, with the words kept
# after them, instead of by the whole source line.
_TALK_LABEL_WORDS = frozenset({'invited', 'talk', 'lecture', 'presentation'})
# Words naming how the talk was given ("<venue>. <series>. Virtual. Invited
# talk.", PZTNWT 158). They count as label words only beside a role or event:
# alone, "Emory University, 2022 (Virtual)" would be titled a bare "Virtual",
# which reads as the talk's title and quiets the doctor's missing-title lint
# (1BFKKK 106, #1346).
_DELIVERY_WORDS = frozenset({'virtual'})
_TITLE_EDGES = ' \t,;:|-–—'
# A stop can only open a title once a venue that led the line is gone
# ("<venue>. <series>. Invited talk."); at the end it may close one.
_TITLE_START_EDGES = _TITLE_EDGES + '.'
_SEPARATOR_RUN_RE = re.compile(r'\s*([,;:|\-–—])(?:\s*[,;:|.\-–—])+\s*')
# A phrase's words and its punctuation marks, one token each.
_PHRASE_TOKEN_RE = re.compile(r'\w+|[^\w\s]')
_WORD_CHAR_RE = re.compile(r'\w')


def _normalize_space(text: str) -> str:
    """Collapse whitespace runs (source lines are often tab-aligned) and fold
    curly apostrophes so field values match the line they were taken from."""
    return re.sub(r'\s+', ' ', text.replace('\u2019', "'").replace('\u2018', "'")).strip()


def _phrase_pattern(phrase: str) -> str:
    """A regex for `phrase` that lets the source space a punctuation mark
    differently: stage 4 wrote "SFB960/RCB Colloquium" for the source's
    "SFB960 /RCB colloquium" (PZTNWT 151, #1346). Two words still need
    whitespace between them."""
    tokens = _PHRASE_TOKEN_RE.findall(phrase)
    pattern = re.escape(tokens[0])
    for before, token in pairwise(tokens):
        between_words = _WORD_CHAR_RE.match(before) and _WORD_CHAR_RE.match(token)
        gap = r'\s+' if between_words else r'\s*'
        pattern += gap + re.escape(token)
    return pattern


def _remove_phrase(text: str, phrase: str) -> str:
    """`text` without each whole-word, case-insensitive occurrence of `phrase`,
    however the source spaces its punctuation (`_phrase_pattern`)."""
    if not phrase.strip():
        return text
    return re.sub(rf'(?<!\w){_phrase_pattern(phrase)}(?!\w)', ' ', text, flags=re.IGNORECASE)


def _remove_venue(text: str, venue: str) -> str:
    """`text` without `venue` where it stands as a segment of its own, set off
    by separators or the line's edges. Inside a phrase ("a conference hosted
    by <venue>") it stays, or the phrase would be left cut short."""
    if not venue:
        return text
    return re.sub(
        rf'(?:^|(?<=[,;:|\-–—(]))\s*{re.escape(venue)}(?=\s*(?:[,;:|.\-–—()]|$|\d|{_MONTH}(?!\w)))',
        ' ', text, flags=re.IGNORECASE)


def _year_is_shown(year: str, shown_years: set[str]) -> bool:
    """Whether the Dates cell shows `year`; a two-digit year ("04") matches the
    shown year it ends ("2004")."""
    if len(year) == _SHORT_YEAR_DIGITS:
        return any(shown.endswith(year) for shown in shown_years)
    return year in shown_years


def _strip_leading_date(line: str, shown_years: set[str]) -> str:
    """`line` without the year or numeric date that opens it, when the Dates
    cell shows its year."""
    for pattern in (_LEADING_YEAR_RE, _LEADING_NUMERIC_DATE_RE):
        match = pattern.match(line)
        if match and _year_is_shown(match.group(1), shown_years):
            return line[match.end():]
    return line


def _shown_phrases(venue: str, role: str, event_name: str) -> list[str]:
    """The phrases of the source line the row already shows, in the order
    they are taken out: the whole venue, role and event name (longest
    first), then each comma, semicolon or bracket piece of the three
    (longest first), so a whole value goes before its parts and a value
    goes before one it contains: the venue "Cold Spring Harbor" must not
    break the event "Cold Spring Harbor Meeting on ..." (PZTNWT 132, #1346)."""
    wholes = [_normalize_space(v or '') for v in (venue, role, event_name)]
    pieces = {piece for whole in wholes for piece in _VALUE_PIECE_SPLIT_RE.split(whole)}
    ordered = [*sorted(wholes, key=len, reverse=True),
               *sorted(pieces, key=len, reverse=True)]
    return [phrase for phrase in ordered if phrase]


def _untitled_talk_title(text: str, role: str, event_name: str, venue: str,
                         date_cell: str) -> str:
    """The Title cell of an R entry stage 4 found no title for.

    The source line restates what the Institution and Dates cells already
    show. Strip its list number, the year or numeric date the Dates cell
    shows when it opens or closes the line, and the venue. When nothing is
    left beyond role, event name, venue (or a piece of any of them), date
    words and words naming the kind of appearance, the title is role + event
    name ("Visiting Professor, <event>"), then those words ("Invited talk");
    anything more may be the talk title stage 4 missed (#475), so the
    stripped line renders instead.
    """
    shown_years = set(_YEAR_RE.findall(date_cell or ''))
    line = _LIST_NUMBER_RE.sub('', _normalize_space(text or ''), count=1)
    line = _strip_leading_date(line, shown_years)
    line = _remove_venue(line, _normalize_space(venue or ''))
    trailing_date = _TRAILING_DATE_RE.search(line)
    if trailing_date and trailing_date.group(1) in shown_years:
        line = line[:trailing_date.start()]

    residue = line
    for phrase in _shown_phrases(venue, role, event_name):
        residue = _remove_phrase(residue, phrase)
    words = _WORD_RE.findall(_DATE_WORD_RE.sub(' ', residue))
    label_words = _TALK_LABEL_WORDS
    if (role or '').strip() or (event_name or '').strip():
        label_words = _TALK_LABEL_WORDS | _DELIVERY_WORDS
    if not any(w.casefold() not in _CONNECTIVES | label_words for w in words):
        label = ' '.join(w for w in words if w.casefold() in label_words)
        # The stage-4 values as given, so the venue's own duplicate check
        # in `_fill_presentations` recognises them in the title.
        return ', '.join(part.strip() for part in (role or '', event_name or '', label)
                         if part.strip())
    title = _SEPARATOR_RUN_RE.sub(r'\1 ', _normalize_space(line)).strip(_TITLE_EDGES)
    return title.lstrip(_TITLE_START_EDGES)


# The field names a talk's date arrives under, most specific first: the
# Dates cell's (`presentation_date_spans`).
_TALK_DATE_KEYS = PRESENTATION_DATE_FIELDS
# The field names the meeting that hosted a talk arrives under: R's own, then
# S8's, for an abstract a hierarchy mismatch moved here (#1570, YUYVIG
# SQMWHM 1047 rendered without its meeting).
_TALK_EVENT_KEYS = PRESENTATION_EVENT_FIELDS


def _first_value(fields: Mapping, keys: tuple[str, ...]) -> object:
    """The first filled value of `keys`, as stored, else ''."""
    return next((fields[key] for key in keys if fields.get(key)), '')


def _talk_event_value(fields: Mapping) -> object:
    """The first filled `_TALK_EVENT_KEYS` value, as stored, else ''."""
    return _first_value(fields, _TALK_EVENT_KEYS)


def _talk_date_cell(fields: dict) -> str:
    """The Dates cell of an R entry: the dates `presentation_date_spans`
    lists (its own date, then each further date stage 4 kept under an
    off-schema span key that reads differently), joined with
    `DATE_SPAN_SEPARATOR`."""
    return DATE_SPAN_SEPARATOR.join(presentation_date_spans(fields, 'R'))


class PresentationsSection:
    """Section R writers, mixed into `WCMTemplateGenerator`."""

    def _fill_presentations(self, entries: list[dict]) -> None:
        """Fill R. INVITATIONS TO SPEAK/PRESENT section.

        WCM template has tables for Regional/National/International:
        Table structure: Title | Institution/Location | Dates (yyyy)

        Uses cv_owner_location to classify geographic scope of each entry.
        Each row is recorded against its entry in the row-receipt ledger.
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
            self._clear_table_data(table, keep_header=True)
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

                title = _first_value(fields, PRESENTATION_TITLE_FIELDS)
                institution = _first_value(fields, PRESENTATION_VENUE_FIELDS)

                formatted_date = _talk_date_cell(fields)

                role = str(fields.get('role') or '').strip()
                if role.lower() == 'none':
                    role = ''

                # A split record's own line, not every record's (#1445).
                source_line = fallback_text(entry)
                if not title:
                    title = _untitled_talk_title(
                        source_line, role,
                        str(_talk_event_value(fields)), institution, formatted_date)
                # "(postponed due to COVID-19)" has no field, so stage 4 drops
                # it and the talk would read as delivered (#1436).
                title = with_event_status(title, source_line, institution)

                # The R block is Title | Institution/Location | Dates, so the
                # meeting that hosted the talk has no column of its own and
                # belongs with the venue: "ASMBS 2022 Presidential Grand Rounds,
                # Dallas, TX". event_name was read nowhere in this file, so
                # 2,194 of 2,956 extracted values across 79 CVs reached no part
                # of the rendered document.
                event_name = (_talk_event_value(fields) or '').strip()
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
                self._row_receipts.wrote(row._tr, entry)  # the talk's own row (#534, #1205)
