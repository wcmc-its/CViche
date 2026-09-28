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

logger = logging.getLogger(__name__)

# The document-uid convention this module parses: an optional "CV_" prefix,
# then underscore-separated name/year parts (e.g. "CV_2015_Wende",
# "Wende_John"). `removeprefix` strips only a leading match; a bare
# `.replace('CV_', '')` would also eat "CV_" occurring mid-string in a name
# part, which `removeprefix` cannot do.
_UID_PREFIX = 'CV_'


def _extract_name_from_uid(uid: str) -> str:
    """Extract formatted name from document UID."""
    # Remove year prefix (e.g., "2015_Wende" -> "Wende")
    parts = uid.removeprefix(_UID_PREFIX).split('_')

    # Filter out year
    parts = [p for p in parts if not p.isdigit() and len(p) > 2]

    if len(parts) >= 2:
        # Assume "First_Last" or "Last_First"
        return ' '.join(parts).title()
    elif parts:
        return parts[0].title()
    return uid


def _extract_last_name_from_uid(uid: str) -> str:
    """Extract last name from document UID for author matching."""
    # Remove year prefix (e.g., "2015_Wende" -> "Wende")
    parts = uid.removeprefix(_UID_PREFIX).split('_')

    # Filter out years and very short parts
    parts = [p for p in parts if not p.isdigit() and len(p) > 2]

    if parts:
        # Last part is typically the last name
        last_name = parts[-1]
        # Handle cases like "Albrechtjs" -> "Albrecht" (initials appended).
        #
        # #665 item 1 flags this as unsound: `suffix.islower() or
        # suffix.isupper()` matches the tail of almost any Title Case word,
        # so no case-based rule can tell "appended initials" apart from
        # "the end of an ordinary surname" (its own stated conclusion).
        # Deliberately left in place rather than "fixed" by deletion: this
        # exact case is corpus-real (uid "2003_Albrechtjs_Cv") and the
        # owner's real surname genuinely is "Albrecht" -- confirmed by
        # "Albrecht JS"/"Albrecht J" as the cited author in every one of
        # that CV's own bibliography entries. `bibliography.py:176` uses
        # this function's return value to decide which citation author to
        # bold as the CV owner, so removing the strip does not fix a false
        # positive here -- render_gate_compare over the full 66-CV corpus
        # showed it silently drops bold-highlighting from 12 of the 98
        # paragraphs in that document's bibliography citing "Albrecht": the
        # 12 formatted as bare "Albrecht J"/"Albrecht JS" (which the
        # stripped "Albrechtjs" no longer matches), not the other 86, whose
        # bolding must key off something else (full-name or first-name
        # matching) unaffected by this function's return value. 12 matches
        # what commit 6935094's body and PR_BODY.md already said; this
        # comment (and the test docstring) previously said 10, an error
        # caught in review and corrected here after re-running the
        # python-docx run-level bold diff directly (ref arm vs a probe
        # render with this strip disabled) rather than trusting either
        # number. With zero corresponding case in-corpus where the strip
        # was itself the bug, no signal available inside this function
        # (case pattern, part count, the filtered-out non-name parts)
        # distinguishes the two cases; a real
        # fix belongs at the call site (matching both the raw and stripped
        # candidates against actual citation text) rather than a blind guess
        # made here. See PR body for the corpus evidence in full; #665 item
        # 1 stays open.
        if len(last_name) > 5:
            for suffix_len in [2, 3]:
                suffix = last_name[-suffix_len:]
                if suffix.islower() or suffix.isupper():
                    base = last_name[:-suffix_len]
                    if len(base) >= 3:
                        return base.title()
        return last_name.title()
    return ''


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
    month_year = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text)
    if month_year:
        return month_year.group(2)

    # Pattern 2: Year in parentheses at end (e.g., "(2021)")
    paren_year = re.search(r'\((\d{4})\)\s*$', text)
    if paren_year:
        return paren_year.group(1)

    # Pattern 3: Year range - take the end year (e.g., "2019-2021")
    year_range = re.search(r'(\d{4})\s*[-–—]\s*(\d{4})', text)
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
    memberships = []
    membership_types = []
    organizations = []
    dates = []

    # Common membership type indicators
    membership_keywords = ['member', 'fellow', 'diplomat', 'associate', 'elected', 'honorary']
    date_pattern = re.compile(r'^(\d{1,2}/?\d{0,4}\s*-\s*(?:present|\d{1,2}/?\d{0,4}))$|^(\d{4}\s*-\s*(?:present|\d{4}))$', re.IGNORECASE)

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
                    membership_types.append(part)
                elif date_pattern.match(part) or re.match(r'^\d{1,2}/\d{4}', part):
                    dates.append(part)
                else:
                    organizations.append(part)
        else:
            # No pipe - classify by content
            if any(kw in line.lower() for kw in membership_keywords) and len(line.split()) <= 3:
                membership_types.append(line)
            elif date_pattern.match(line) or re.match(r'^\d{1,2}/\d{4}', line):
                dates.append(line)
            elif _looks_like_organization(line):
                organizations.append(line)

    # Match up memberships - pair organizations with types and dates
    if organizations:
        for i, org in enumerate(organizations):
            mem_type = membership_types[i] if i < len(membership_types) else ''
            date = dates[i] if i < len(dates) else ''
            memberships.append((mem_type, org, date))

    return memberships


class ParsedActivityLine(NamedTuple):
    """One row parsed out of a flattened committee/leadership source table.

    `activity` is the line with any role/date parenthetical stripped out,
    `roles` holds the parenthetical titles in source order, and `dates` is the
    range the line carried -- or was paired with from the orphaned-date pool.
    """
    activity: str
    roles: tuple[str, ...]
    dates: str


# Column-header labels that survive table flattening as their own lines.
_COMMITTEE_HEADER_LABELS = ('dates', 'role', 'committee', 'institution')
# A line that is nothing but a year or a year range ("1999", "1999-2010").
_DATE_ONLY_LINE = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)$', re.IGNORECASE)
# A year or a year range at the end of a line ("Committee    1999-2010").
_TRAILING_DATE = re.compile(r'(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)\s*$', re.IGNORECASE)
# Parenthetical role+date: "(Chair 1999-2010)" or "(Vice Chair 2006-2008 )".
_PAREN_ROLE_DATE = re.compile(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\s*\)', re.IGNORECASE)


def _parse_flattened_committee_lines(lines: list[str]) -> list[ParsedActivityLine]:
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
                # Last part is a date, rest is the activity
                activity = ' | '.join(parts[:-1])
                pipe_date = parts[-1]
                # Also extract any parenthetical role+date from the activity
                paren_match = _PAREN_ROLE_DATE.search(activity)
                if paren_match:
                    role_text = paren_match.group(1).strip().rstrip(',')
                    clean_activity = _PAREN_ROLE_DATE.sub('', activity).strip()
                    roles = (role_text,) if role_text else ()
                else:
                    clean_activity = activity
                    roles = ()
                items.append(ParsedActivityLine(clean_activity, roles, pipe_date))
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
