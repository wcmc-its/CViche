"""Reading structure out of raw CV text (#398).

Six methods on WCMTemplateGenerator that never touched `self` -- free functions
by behaviour, methods only by where they happened to be written. Each takes text
(or an entry's text) and answers a question about its shape: which year does it
mention, is it a table header rather than data, is it a structural label the WCM
template already provides, how many memberships are fused into it.

The boundary that matters: nothing here builds output. These functions read, they
do not write -- no python-docx object is touched, no WCM section is chosen, no
value is reformatted for display. That is what makes them safe to call from any
section writer and trivial to test with a string literal.

Names keep their leading underscore deliberately. Renaming and relocating in the
same change means a failure cannot be attributed to either; the rename is a
separate, mechanical follow-up.
"""
import logging
import re
from typing import NamedTuple

from unified_pipeline.core.run_id import is_run_id

logger = logging.getLogger(__name__)

# The document-uid convention this module parses: an optional "CV_" prefix,
# then underscore-separated name/year parts (e.g. "CV_2015_Wende",
# "Wende_John"). `removeprefix` strips only a leading match; a bare
# `.replace('CV_', '')` would also eat "CV_" occurring mid-string in a name
# part, which `removeprefix` cannot do.
_UID_PREFIX = 'CV_'
# A uid part shorter than this is a stray token ("Cv"), not a name.
_UID_NAME_PART_MIN_CHARS = 3
# One date grammar for this module (#665 item 5). Every recogniser below --
# the year extractor, the membership date parts, the committee date lines --
# is built from these pieces, so a change to a dash or to how a range ends
# reaches all of them.
_DATE_DASH = r'[-–—]'
_YEAR = r'\d{4}'
_RANGE_END = rf'(?:{_YEAR}|present)'
_YEAR_SPAN = rf'{_YEAR}\s*{_DATE_DASH}\s*{_RANGE_END}'
_YEAR_OR_SPAN = rf'{_YEAR}(?:\s*{_DATE_DASH}\s*{_RANGE_END})?'
_MONTH_YEAR_DATE = r'\d{1,2}/?\d{0,4}'
_MONTH_YEAR_SPAN = rf'{_MONTH_YEAR_DATE}\s*{_DATE_DASH}\s*(?:present|{_MONTH_YEAR_DATE})'
_MEMBERSHIP_DATE_RE = re.compile(
    rf'^(?:{_MONTH_YEAR_SPAN}|{_YEAR_SPAN})$', re.IGNORECASE)
_MONTH_NAMES = (r'January|February|March|April|May|June|July|August|'
                r'September|October|November|December')

# Appended-initials guess: only names longer than this are considered, the
# initials are 2 or 3 characters, and at least this many characters must remain.
_APPENDED_INITIALS_MIN_NAME_CHARS = 5
_APPENDED_INITIALS_LENGTHS = (2, 3)
_APPENDED_INITIALS_MIN_BASE_CHARS = 3


def _extract_name_from_uid(uid: str) -> str:
    """Extract formatted name from document UID; '' for a run id (#457), which
    names nobody -- the cover then reads as missing, not as the title-cased id."""
    stem = uid.removeprefix(_UID_PREFIX)
    if is_run_id(stem):
        return ''
    # Remove year prefix (e.g., "2015_Wende" -> "Wende")
    parts = stem.split('_')

    # Filter out year
    parts = [p for p in parts if not p.isdigit() and len(p) > 2]

    if len(parts) >= 2:
        # Assume "First_Last" or "Last_First"
        return ' '.join(parts).title()
    elif parts:
        return parts[0].title()
    return uid


def _extract_last_name_from_uid(uid: str) -> str:
    """Extract last name from document UID for author matching.

    Returns the uid's last name part as written. It does NOT guess whether the
    part carries appended initials ("Quennevillejs"): no case rule can tell those
    from the tail of an ordinary surname ("Smith"), so the guess lives at the
    one call site that can check it against real citation text --
    `_resolve_uid_owner_surname` in sections/bibliography.py, which uses
    `_strip_appended_initials` below (#665 item 1).

    Returns '' for a run id (#457): it names nobody, and a letters-only one
    would otherwise become a surname to match citation authors against.
    """
    stem = uid.removeprefix(_UID_PREFIX)
    if is_run_id(stem):
        return ''
    parts = stem.split('_')

    # Filter out years and very short parts
    parts = [p for p in parts if not p.isdigit() and len(p) >= _UID_NAME_PART_MIN_CHARS]

    if parts:
        # Last part is typically the last name
        return parts[-1].title()
    return ''


def _strip_appended_initials(last_name: str) -> str:
    """`last_name` without 2-3 trailing characters that might be appended
    initials ("Quennevillejs" -> "Quenneville"), or unchanged when none qualify.

    A guess, not a fact: the tail of "Smith" passes the same case test and
    would become "Smi". Callers must confirm the result against text that
    names the person (a citation) before trusting it.
    """
    if len(last_name) > _APPENDED_INITIALS_MIN_NAME_CHARS:
        for suffix_len in _APPENDED_INITIALS_LENGTHS:
            suffix = last_name[-suffix_len:]
            if suffix.islower() or suffix.isupper():
                base = last_name[:-suffix_len]
                if len(base) >= _APPENDED_INITIALS_MIN_BASE_CHARS:
                    return base.title()
    return last_name.title()


def _extract_year_from_text(text: str) -> str | None:
    """Extract year from raw text as fallback when not in extracted_fields.

    Looks for patterns like:
    - "August 2021"
    - "December 2017"
    - "May 2015"
    - "(2021)"
    - "2019-2021"
    """
    if not text:
        return None

    # Pattern 1: Month Year (e.g., "August 2021", "December 2017")
    month_year = re.search(rf'({_MONTH_NAMES})\s+({_YEAR})', text)
    if month_year:
        return month_year.group(2)

    # Pattern 2: Year in parentheses at end (e.g., "(2021)")
    paren_year = re.search(rf'\(({_YEAR})\)\s*$', text)
    if paren_year:
        return paren_year.group(1)

    # Pattern 3: Year range - take the end year (e.g., "2019-2021")
    year_range = re.search(rf'({_YEAR})\s*{_DATE_DASH}\s*({_YEAR})', text)
    if year_range:
        return year_range.group(2)

    # Pattern 4: Single year in text
    single_year = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if single_year:
        return single_year.group(1)

    return None


# Words a column header may carry besides its section's keywords: "Name of
# award", "Date awarded (yyyy)", "Year | Committee Name". They are column-label
# vocabulary, not content: a cell made ONLY of keywords and these is a header,
# and a cell with any other word ("Best Teaching Award") is not.
_HEADER_FILLER_WORDS = frozenset({
    'name', 'of', 'the', 'and', 'or', 'awarded', 'received', 'issued', 'yyyy',
    'mm', 'yy', 'dd', 'mm/yy', 'mm/yyyy', 'title', 'position', 'role', 'type',
    'location', 'activity', 'committee', 'journal', 'service', 'served',
    'period', 'years', 'year', 'current', 'last', 'known', 'degree', 'amount',
    'nature', 'in', 'to', 'from', 'present', 'supervised', 'attended',
    'obtained', 'specialization', 'department', 'mentor', 'training',
    'student', 'dissertation', 'essay', 'project', 'course', 'description',
    'person', 'month', 'body'})


def _is_header_cell(cell: str, header_keywords: list[str]) -> bool:
    """Is every word of this cell a header keyword or a header filler word?

    A cell holding a year is a date value, which a header row names but never
    carries.
    """
    if re.search(r'\b\d{4}\b', cell):
        return False
    words = re.findall(r"[a-z/]+", cell.replace('(s)', 's'))
    if not words:
        return False
    vocabulary = _HEADER_FILLER_WORDS | {kw.lower() for kw in header_keywords}
    return all(w in vocabulary or (w.endswith('s') and w[:-1] in vocabulary)
               for w in words)


def _is_table_header_entry(text: str, header_keywords: list[str], threshold: int = 2) -> bool:
    """Detect if an entry is actually a table header that was mistakenly extracted as data.

    Table headers are characterized by:
    - Multiple header-like words (e.g., "Name of award", "Date", "Organization")
    - Tab or pipe-separated columns
    - No substantive content (just column labels)

    Args:
        text: The entry text to check
        header_keywords: List of keywords that typically appear in headers for this section
        threshold: Minimum number of header keywords required to classify as header

    Returns:
        True if this appears to be a table header, False otherwise
    """
    if not text:
        return False

    # Normalize text for checking
    text_lower = text.lower().strip()

    # If text is very short, it might be header-like
    # But only if it matches header patterns
    if len(text_lower) < 100:
        # Count how many header keywords appear as whole words -- a substring
        # test would match "date" inside "candidate" or "organization" inside
        # "Organization of Medical Education", both real content, not headers.
        # A trailing optional "s" keeps this matching a plural header word
        # ("Dates" for keyword "date") the same way `header_patterns` below
        # already does via `dates?` -- without it, a bare `\bdate\b` regexp
        # stops matching "Dates" entirely (word-boundary matching removes
        # the plural along with the "candidate" false positive it was meant
        # to fix).
        keyword_count = sum(
            1 for kw in header_keywords
            if re.search(rf'\b{re.escape(kw.lower())}s?\b', text_lower)
        )

        # Check for common header patterns
        header_patterns = [
            r'\bname\s+of\s+',  # "Name of award", "Name of organization"
            r'\bdate\s*(awarded|received|of|issued)?\b',  # "Date awarded", "Date of issue"
            r'\b(organization|institution)\s*(name)?\b',  # "Organization", "Institution name"
            r'\btitle\b.*\b(institution|organization|dates?)\b',  # "Title | Institution | Dates"
            r'\bdates?\s*\(?[mdy/]+\)?',  # "Dates (mm/yy)"
        ]

        pattern_matches = sum(1 for p in header_patterns if re.search(p, text_lower))

        # If multiple header keywords AND pattern matches, likely a header
        if keyword_count >= threshold and pattern_matches >= 1:
            return True

        # Also check for tab/pipe-separated header-only content. A header row
        # is a header only on POSITIVE evidence: every cell is made of header
        # words (a section keyword or a filler word such as "name of"). One
        # cell with any other word ("Best Teaching Award", "Purdue University")
        # or a year is content, so the entry is data (#756).
        if '\t' in text or '|' in text:
            cells = [c for c in re.split(r'[\t|]', text_lower) if c.strip()]
            if cells and all(_is_header_cell(c, header_keywords) for c in cells):
                return True

    return False


def _is_structural_label(entry: dict) -> bool:
    """Check if an entry is a structural label from the source CV rather than actual content.

    Source CVs contain section headers, sub-headers, and structural labels
    (e.g., "CLINICAL PRACTICE ACTIVITIES", "Direct Teaching/Precepting/Supervision")
    that sometimes get extracted as entries. These should not appear as content
    in the WCM output — the WCM template provides its own structure.

    Checks:
    1. All-caps text longer than 3 characters, corroborated by either the
       entry's own hierarchy labels (section headers) or a stage-4
       extraction that found no substantive fields for it at all
    2. Entry text that exactly matches one of its own hierarchy labels
    """
    text = (entry.get('text', '') or '').strip()
    if not text:
        # Blank raw text is a label only when stage 5c has nothing to say
        # either; an entry with real formatted_text is content (#757). The
        # hierarchy and all-caps checks below need raw text, so return here.
        fields = entry.get('extracted_fields')
        formatted = fields.get('formatted_text') if isinstance(fields, dict) else None
        return not (isinstance(formatted, str) and formatted.strip())

    text_lower = text.lower()
    hierarchy = entry.get('hierarchy', []) or []

    # Text that exactly matches one of its hierarchy labels
    for label in hierarchy:
        if text_lower == label.strip().lower():
            return True

    # All-caps text (section headers like "CLINICAL PRACTICE ACTIVITIES").
    # Case alone can't tell a header from legitimate all-caps content (a
    # name, "USA", an org name written in caps) -- require corroborating
    # evidence before treating it as a header rather than dropping every
    # long all-caps run unconditionally.
    if text == text.upper() and len(text) > 3 and not any(c.isdigit() for c in text):
        # Signal 1: the text echoes one of the entry's own hierarchy labels.
        for label in hierarchy:
            label_lower = label.strip().lower()
            if label_lower and (label_lower in text_lower or text_lower in label_lower):
                return True

        # Signal 2: stage 4 attempted extraction on this entry and came back
        # with nothing -- every field it looked for is null. A hierarchy-echo
        # match alone misses a real corpus case: a stray section-header
        # string (e.g. "CLINICAL PRACTICE ACTIVITIES") extracted as an entry
        # *under a different section's hierarchy* than its own (a stage 2/3
        # misclassification -- see `hierarchy_mismatch_flag` on such
        # entries), which by construction never echoes the hierarchy it was
        # filed under. It also never carries any real field value, since
        # there was never any content to extract. Genuine all-caps content
        # (a name, an org) that reaches this function always has at least
        # one populated field or is missing `extracted_fields` altogether
        # (untested/synthetic callers) -- neither case trips this signal.
        # `extracted_fields` must be present and non-None to count: an
        # absent key means extraction was never attempted for this entry,
        # which is not evidence of "nothing to extract".
        fields = entry.get('extracted_fields')
        if isinstance(fields, dict) and fields and not any(fields.values()):
            return True

    return False


# A pipe-free membership part that is not an organization even though it is
# neither a type nor a range: a bare year or month-year, and the column-header
# / filler / role words a flattened table leaves behind. This is the positive
# form of what a `len(line) > 5` cutoff used to reject by accident (#758) --
# the same cutoff also dropped real acronym organizations (AMA, NIH, ASCO).
_BARE_DATE_PART_RE = re.compile(
    r'^\(?(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?'
    r'\d{1,4}(?:/\d{1,4})?\)?[.,;]?$',
    re.IGNORECASE,
)
_NON_ORGANIZATION_WORDS = frozenset({
    'date', 'dates', 'year', 'years', 'role', 'roles', 'title', 'type', 'name',
    'organization', 'organizations', 'society', 'societies', 'position',
    'present', 'current', 'ongoing', 'to', 'none', 'yes', 'no', 'n/a', 'na',
    'chair', 'co-chair', 'board', 'other',
})


# At or below this length a pipe-free part is an organization only in the slot
# right after a membership type ("Member" then "AMA"). Anywhere else a short
# line is a location or acronym trailing the organization before it ("NY",
# "USA", "(AHA)"): counting it would shift every later type and date onto the
# wrong row, since the three lists are paired by position (#758 follow-up).
_SHORT_ORGANIZATION_MAX_CHARS = 5


def _looks_like_organization(part: str) -> bool:
    """True when a pipe-free membership part reads as an organization name.

    Needs a run of two letters (so "-", "2005", "(1)" fail), must not be a bare
    year / month-year, and must not be one of the column-header or filler words
    a flattened table leaves as its own line. "AMA", "NIH" and "IEEE" pass.
    """
    stripped = part.strip()
    if not re.search(r'[^\W\d_]{2}', stripped):
        return False
    if _BARE_DATE_PART_RE.match(stripped):
        return False
    return stripped.lower().rstrip(':.') not in _NON_ORGANIZATION_WORDS


def _parse_multi_membership_entry(lines: list[str]) -> list[tuple[str, str, str]]:
    """Parse multiple memberships from merged entry lines.

    Handles patterns like:
    - "Member | Org1 | date1" per line
    - "Member\\nElected Member | Org1\\nOrg2 | date1\\ndate2"

    Returns:
        List of (membership_type, organization, dates) tuples
    """
    membership_types: list[_PlacedValue] = []
    organizations: list[str] = []
    dates: list[_PlacedValue] = []

    # Common membership type indicators
    membership_keywords = ['member', 'fellow', 'diplomat', 'associate', 'elected', 'honorary']

    after_type = False
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if line has pipe separators (structured format)
        if '|' in line:
            parts = [p.strip() for p in line.split('|')]
            for part in parts:
                if not part:
                    continue
                if any(kw in part.lower() for kw in membership_keywords) and len(part.split()) <= 3:
                    membership_types.append(_PlacedValue(part, len(organizations)))
                elif _MEMBERSHIP_DATE_RE.match(part) or re.match(r'^\d{1,2}/\d{4}', part):
                    dates.append(_PlacedValue(part, len(organizations)))
                else:
                    organizations.append(part)
        else:
            # No pipe - classify by content
            if any(kw in line.lower() for kw in membership_keywords) and len(line.split()) <= 3:
                membership_types.append(_PlacedValue(line, len(organizations)))
                after_type = True
                continue
            if _MEMBERSHIP_DATE_RE.match(line) or re.match(r'^\d{1,2}/\d{4}', line):
                dates.append(_PlacedValue(line, len(organizations)))
            elif _looks_like_organization(line) and (
                    after_type or len(line) > _SHORT_ORGANIZATION_MAX_CHARS):
                organizations.append(line)
            after_type = False

    return _pair_memberships(membership_types, organizations, dates)


class _PlacedValue(NamedTuple):
    """A membership type or date, and how many organizations had been seen
    when it was read -- the only evidence of where in the source it sat."""
    value: str
    organizations_before: int


def _placed_or_blank(placed: list[_PlacedValue], organization_count: int,
                     organizations_before_own: int, what: str) -> list[str]:
    """One value per organization; a value is kept only where position proves it.

    Types and dates are collected as separate lists and used to be matched to
    the organizations by index. That is only sound while each list has one
    entry per organization. When the counts agree the column layout is
    trusted, as before. When they disagree (a wrapped line, a missing date, an
    extra header) a value is kept only if the source order backs it: value `j`
    sat after `organizations_before_own + j` organizations, i.e. right where a
    "type | organization | date" row puts it. Every other value is left off
    rather than shifted onto a neighbouring row -- a wrong value looks as
    confident as a right one in the document. Mismatches are logged (same rule
    as `_parse_flattened_committee_lines`, #665 item 6).
    """
    if len(placed) == organization_count:
        return [p.value for p in placed]
    if placed:
        logger.warning(
            "membership %s alignment: %d value(s) for %d organization(s) -- "
            "counts disagree, keeping only values whose source position "
            "matches their organization", what, len(placed), organization_count)
    aligned = [''] * organization_count
    for j, p in enumerate(placed[:organization_count]):
        if p.organizations_before == organizations_before_own + j:
            aligned[j] = p.value
    return aligned


def _pair_memberships(membership_types: list[_PlacedValue], organizations: list[str],
                      dates: list[_PlacedValue]) -> list[tuple[str, str, str]]:
    """(type, organization, dates) per organization; see `_placed_or_blank`.

    A type is read before its organization in a row, a date after it.
    """
    count = len(organizations)
    types = _placed_or_blank(membership_types, count, 0, 'type')
    paired_dates = _placed_or_blank(dates, count, 1, 'date')
    return list(zip(types, organizations, paired_dates))


class ParsedActivityLine(NamedTuple):
    """One row parsed out of a flattened committee/leadership source table.

    `activity` is the line with any role/date parenthetical stripped out,
    `roles` holds the parenthetical titles in source order, and `dates` is the
    range the line carried -- or was paired with from the orphaned-date pool.
    `institution` is the source table's institution/location cell, and is
    filled only when the caller says the table has that column between the
    activity and the date (`institution_column=True`, #664); '' otherwise.
    """
    activity: str
    roles: tuple[str, ...]
    dates: str
    institution: str = ''


# Column-header labels that survive table flattening as their own lines.
_COMMITTEE_HEADER_LABELS = ('dates', 'role', 'committee', 'institution')
# A line that is nothing but a year or a year range ("1999", "1999-2010").
_DATE_ONLY_LINE = re.compile(rf'^({_YEAR_OR_SPAN})$', re.IGNORECASE)
# A year or a year range at the end of a line ("Committee    1999-2010").
_TRAILING_DATE = re.compile(rf'({_YEAR_OR_SPAN})\s*$', re.IGNORECASE)
# Parenthetical role+date: "(Chair 1999-2010)" or "(Vice Chair 2006-2008 )".
_PAREN_ROLE_DATE = re.compile(
    rf'\(([^)]*?)({_YEAR})\s*{_DATE_DASH}\s*({_RANGE_END})\s*\)', re.IGNORECASE)


def _parse_pipe_date_row(cells: list[str], pipe_date: str,
                         institution_column: bool) -> ParsedActivityLine:
    """One "cell | cell | date" row whose last cell is the date column.

    Without `institution_column` every cell before the date is the activity
    (joined back with " | ", as before #664). With it, the first cell is the
    activity and the cells between it and the date are the institution: a
    Section O row reads "Role | Institution | Dates". Any role+date
    parenthetical is lifted out of the activity cell either way.
    """
    institution = ''
    if institution_column and len(cells) >= 2:
        activity, institution = cells[0], ', '.join(cells[1:])
    else:
        activity = ' | '.join(cells)
    paren_match = _PAREN_ROLE_DATE.search(activity)
    if not paren_match:
        return ParsedActivityLine(activity, (), pipe_date, institution)
    role_text = paren_match.group(1).strip().rstrip(',')
    clean_activity = _PAREN_ROLE_DATE.sub('', activity).strip()
    roles = (role_text,) if role_text else ()
    return ParsedActivityLine(clean_activity, roles, pipe_date, institution)


def _parse_flattened_committee_lines(
    lines: list[str], *, institution_column: bool = False,
) -> list[ParsedActivityLine]:
    """Parse committee/leadership lines flattened out of a source table.

    The single line parser behind section O's `_add_multiline_leadership_rows`
    and section P's `_add_multiline_committee_rows` (#572 -- P was a drifted
    copy that dropped dates for three of these shapes). Handles what a source
    table looks like once its column structure is gone:

    - "Committee Name (Chair 1999-2010)" -- parenthetical role+date
    - "Committee Name | 1999-2010" -- pipe-separated date column
    - "Committee Name    1999-2010" -- trailing date
    - "1999-2010" -- a date whose activity is on another line

    The last shape feeds `dates_pool`: when column 1 and column 2 of a source
    table arrive as separate runs of lines, the dates are orphaned and are
    matched back to date-less items by position, forward, because both columns
    come from the same table and are therefore in the same order -- *when*
    the two runs are the same length. If extraction drops or inserts a line
    (an extra header, a split row) the counts disagree and position no
    longer proves the two runs still line up; pairing anyway would put a
    real date on the wrong activity, which is worse than no date at all
    because a wrong date looks exactly as confident as a right one once it's
    in the document. So the forward pairing only runs when the counts match;
    otherwise the affected items stay dateless (review thread 3843817401 /
    #665) and a warning is logged naming the mismatch.

    A line carrying several parentheticals ("(Vice Chair 2006-2008) (Chair
    2008-2010)") is one role held under changing titles, so it becomes one
    item: the latest date range, with every title collected into `roles`.

    `institution_column=True` (Section O, #664) reads "Role | Institution |
    Dates" pipe rows into `ParsedActivityLine.institution`; the default keeps
    every cell in the activity, which is what Section P wants -- its middle
    column is Role, not institution.
    """
    items: list[ParsedActivityLine] = []
    dates_pool: list[str] = []

    for line in lines:
        # entry_lines() already strips every line at both call sites, but this
        # is a shared pure-parsing function (#625 review) -- don't depend on
        # that; normalize here too so a header label survives incidental
        # whitespace regardless of caller.
        line = line.strip()
        if not line or line.lower() in _COMMITTEE_HEADER_LABELS:
            continue

        # Handle pipe separator from table column extraction
        # e.g., "Committee (Chair 2002-present) | 1996-Present"
        if '|' in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 2 and _TRAILING_DATE.match(parts[-1]):
                items.append(_parse_pipe_date_row(
                    parts[:-1], parts[-1], institution_column))
                continue
            elif len(parts) == 1:
                line = parts[0]
            # else fall through to normal processing

        # Check if this is a date-only line
        if _DATE_ONLY_LINE.match(line):
            dates_pool.append(line)
            continue

        # Check for parenthetical role+date: "Committee (Chair 1999-2010)"
        paren_matches = list(_PAREN_ROLE_DATE.finditer(line))
        if paren_matches:
            clean_activity = _PAREN_ROLE_DATE.sub('', line).strip()
            if len(paren_matches) > 1:
                # Multiple parentheticals on the same line: latest date range,
                # every named title kept
                last = paren_matches[-1]
                item_date = f"{last.group(2)}-{last.group(3)}"
                roles = tuple(m.group(1).strip().rstrip(',')
                              for m in paren_matches if m.group(1).strip())
            else:
                match = paren_matches[0]
                item_date = f"{match.group(2)}-{match.group(3)}"
                role_text = match.group(1).strip().rstrip(',')
                roles = (role_text,) if role_text else ()
            items.append(ParsedActivityLine(clean_activity, roles, item_date))
            continue

        # Check if line has embedded date at the end (not in parentheses)
        date_match = _TRAILING_DATE.search(line)
        if date_match:
            item_text = line[:date_match.start()].strip()
            item_date = date_match.group(1)
            if item_text:
                items.append(ParsedActivityLine(item_text, (), item_date))
            else:
                # Just a date with no text - add to pool
                dates_pool.append(item_date)
            continue

        # Plain text line - no date found
        items.append(ParsedActivityLine(line, (), ''))

    # Match dates_pool to items without dates using forward mapping. Both
    # items and dates come from the same source table (column 1 -> items,
    # column 2 -> dates), and are always in the same order -- but only when
    # the two runs are the same length is that order still provable. Pair
    # positionally only on a matching count; on a mismatch, leave the
    # dateless items dateless rather than shift a real date onto the wrong
    # activity (review thread 3843817401 / #665). A dateless item renders
    # with a blank Dates cell -- a visible gap, not a silent wrong answer.
    undated_count = sum(1 for item in items if not item.dates)
    if dates_pool and undated_count == len(dates_pool):
        date_idx = 0
        for i, item in enumerate(items):
            if not item.dates:
                items[i] = item._replace(dates=dates_pool[date_idx])
                date_idx += 1
    elif dates_pool:
        logger.warning(
            "committee/leadership date alignment: %d orphaned date(s) but "
            "%d undated activity line(s) -- counts disagree, leaving dates "
            "unassigned instead of pairing positionally",
            len(dates_pool), undated_count,
        )

    return items


# A year or a year range at the START of a line ("1999-2010    Committee"): the
# date-prefixed layout, where every record opens with its own date.
_LEADING_DATE = re.compile(rf'^{_YEAR_OR_SPAN}\b', re.IGNORECASE)

# Two dated lines are the smallest text that can hold two records. One dated
# line is one record plus, at most, a wrapped description under it.
_MULTI_RECORD_MIN_DATED_LINES = 2


def _line_carries_record_date(line: str) -> bool:
    """True when `line` carries a date in one of the shapes the flattened-table
    parser reads -- a role+date parenthetical, or a date at the end of the line
    (which covers a bare date line and a pipe row whose last cell is the date
    column) -- or opens with one, which the parser does not read but which
    marks a record start in a date-prefixed block."""
    line = line.strip()
    return bool(_PAREN_ROLE_DATE.search(line) or _TRAILING_DATE.search(line)
                or _LEADING_DATE.match(line))


def _looks_like_multiple_records(lines: list[str]) -> bool:
    """True when an entry's raw lines hold more than one dated record (#660).

    Counts dated lines, not lines: a single committee whose description wraps
    over five lines carries one date, a block of merged records carries one per
    record. Counted line by line rather than from
    `_parse_flattened_committee_lines`, because that parser drops orphaned
    date lines when their count disagrees with the undated activities, and a
    dropped date is exactly the second record this has to see.
    Ambiguity resolves toward "multiple": a description that itself ends in a
    year counts as a record, so the caller re-parses the lines instead of
    trusting a single extracted record that may be one of several. Only a
    block with at most one dated line is called a single record; undated
    activity lines are not counted, so a list of undated records with a single
    dated line reads as one record (judgement call, disclosed on the PR).
    """
    dated = sum(1 for line in lines if _line_carries_record_date(line))
    return dated >= _MULTI_RECORD_MIN_DATED_LINES
