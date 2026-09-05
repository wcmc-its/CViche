"""Section I: professional organizations and society memberships (#398).

Two columns -- organization, dates -- and one recurring shape problem. Source
CVs write memberships as a two-column table, and extraction flattens that into a
single entry whose lines interleave the columns:

    "Member\nElected Member | Org1\nOrg2 | date1\ndate2"

More than two lines is the signal to hand the entry to
`_parse_multi_membership_entry` and emit a row per membership rather than trust
the `extracted_fields`, which describe only the first.

Either way the organization cell is built the same, by `_organization_cell`:
the membership type is prefixed only when it is not already inside the
organization name, so "Fellow, American College of Surgeons" does not become
"Fellow, Fellow of the American College of Surgeons". That containment test is
token/phrase-aware, not substring: a type is "already named" only when its
words appear as a contiguous run of the organization's own words, so "Member"
is still prefixed to an organization that merely contains the letters m-e-m-b-e-r
somewhere inside a longer word.

Dates go through `_membership_dates_cell` on both paths. The multi-membership
parser hands back one raw range string ("2015-present") while the single path
has separate `start_date`/`end_date` fields; `_split_date_range` reduces the
first shape to the second so both end at the same `format_date_range(..., 'I')`
call and equivalent memberships cannot render two different date formats.

An entry with no `organization` field goes to `_organization_fallback`, a
dedicated extractor: it takes the entry's FIRST fragment (newline/tab/pipe),
lifts a trailing date range out of it into the date column, and separates a
leading membership-type prefix so the type is prefixed by the normal path
rather than baked into the organization name -- then lets a structured alias
field override whichever of the two values it names. That replaced a flat
`original_text[:150]`, which put membership type, dates and unrelated trailing
source text into the organization cell. Every fallback is counted in
`stats['membership_organization_fallbacks']` and logged, because a recovered
organization is a lower-confidence cell than an extracted one and the run
should say so.

The organization alias and the membership-type alias are read INDEPENDENTLY.
Resolving them together cost 2082_Dr_Scot both of its memberships' roles: an
`institution` field named the organization, the branch returned on it, and the
`role` field sitting beside it never reached a cell.

That recovery is bounded by one rule, with no exemptions: it may MOVE a word
out of the organization cell -- a date into the date column, a membership type
into the prefix `_organization_cell` writes back -- but it may never lose one.
`_recovery_covers_text` checks it on every entry, alias-supplied or scanned,
and a recovery that fails renders the entry's whole raw text instead, which is
the cell the section rendered before this extractor existed. A misread
fragment, or an alias that names less than the entry does, can therefore cost
a row its improvement, never any of its content.

Finding the table takes two tries. The heading text has changed across template
revisions ("PROFESSIONAL ORGANIZATIONS", "SOCIETY MEMBERSHIPS", "MEMBERSHIPS"),
and if none of them match, the fallback searches for a table whose first cell
says "Organization" -- then checks that its SECOND column header names a date
before writing into it, because "Organization" alone also matches tables
belonging to other sections. That second check goes through
`_is_date_header_cell`, which case- and whitespace-folds the cell first, so
"DATE", "Dates" and "Date  Awarded" are accepted where a literal "Date"
substring test rejected the first two.

Extraction keeps the source table's own header row, so header-shaped entries are
dropped explicitly rather than rendered as a membership called "Organization".

`_add_table_row` is the generic row writer, and it lives here because this is
the only section that calls it. Everything else either writes cells directly or
uses one of the shared `_add_table_row_with_*` variants, which stay on
`WCMTemplateGenerator`. It owns `stats['entries_inserted']` outright -- the
callers below must not also increment it -- and it refuses a `data` list longer
than the table has columns instead of dropping the surplus cells.
"""
import logging
import re
from typing import NamedTuple

try:
    from docx.table import Table
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    _clear_table_data,
    _set_cell_vertical_alignment,
    _set_font,
    format_date_range,
)
from ..parsing import _is_table_header_entry, _parse_multi_membership_entry
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines

logger = logging.getLogger(__name__)

# Section I's taxonomy code -- the key into formatting/dates.py's DATE_FORMATS,
# which maps it to 'yyyy'. Named because it appears in the one date-formatting
# helper below and nowhere else; a bare 'I' at a call site reads like an index.
_MEMBERSHIPS_TAXONOMY_CODE = 'I'

# The vocabulary `_parse_multi_membership_entry` (stage6/parsing/text.py)
# classifies parts with, restated here so `_entry_parts`'s grammar gate accepts
# exactly the splits that parser can actually read. The parser keeps these
# private to its own body; duplicating the two rules is deliberate -- a gate
# that used a DIFFERENT grammar than the consumer would either admit splits the
# parser then mangles, or reject splits it would have read correctly.
_MEMBERSHIP_TYPE_KEYWORDS = ('member', 'fellow', 'diplomat', 'associate', 'elected', 'honorary')
_MEMBERSHIP_TYPE_MAX_WORDS = 3
_DATE_PART_RE = re.compile(
    r'^(\d{1,2}/?\d{0,4}\s*-\s*(?:present|\d{1,2}/?\d{0,4}))$'
    r'|^(\d{4}\s*-\s*(?:present|\d{4}))$',
    re.IGNORECASE,
)
_DATE_PREFIX_RE = re.compile(r'^\d{1,2}/\d{4}')

# One raw range string -> (start, end). Hyphen, en dash and em dash only: a
# slash is part of a date ("1/1997"), not a separator between two.
_DATE_RANGE_SPLIT_RE = re.compile(r'\s*[-–—]\s*')

# Word tokens for the type-already-named test: everything that is not a letter
# or digit is a separator, so "Fellow," "(Fellow)" and "Fellow" tokenize alike.
_TOKEN_SPLIT_RE = re.compile(r'[^0-9a-z]+')

# Header-cell normalization for the "Organization" fallback table's guard.
_HEADER_WHITESPACE_RE = re.compile(r'\s+')
_DATE_HEADER_RE = re.compile(r'\bdates?\b')

# Fallback organization extraction.
_FRAGMENT_SPLIT_RE = re.compile(r'[\n\t|]')
_ORGANIZATION_FALLBACK_MAX_CHARS = 150
# Field names other extraction shapes use for the same value as `organization`.
_ORGANIZATION_FIELD_ALIASES = ('institution', 'organization_name', 'society')
# ...and for the same value as `membership_type`. Stage 4 picks a field schema
# per taxonomy code (stage4/schemas.py), so an entry that reaches section I
# under a different code's schema names the role under that schema's key:
# `role` is the one the farm actually produces (2082_Dr_Scot's memberships
# carry the K-schema's course_code/institution/role shape), and
# `fellowship_designation` is section I's own second designation field.
# `position` is emitted by no schema at all and `title` only ever names a work
# -- a publication, a talk, a patent -- so neither is a membership designation
# and neither is listed here.
_MEMBERSHIP_TYPE_FIELD_ALIASES = ('membership_type', 'fellowship_designation', 'role')
# A written-out month is part of the date, not part of the organization name.
# Full names and the three-letter abbreviations, with an optional period, and
# only ever immediately in front of a year -- so "March 2018" is a date while
# "Marching Band Alumni 2018" keeps "Marching Band Alumni".
_MONTH_NAME_ALTERNATION = (
    r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?'
    r'|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
)
_DATE_ENDPOINT = (
    rf'(?:\b(?:{_MONTH_NAME_ALTERNATION})\.?\s+)?(?:\d{{1,2}}/)?\d{{4}}|present'
)
_TRAILING_DATES_RE = re.compile(
    r'[\s,;:(\[]*'
    rf'({_DATE_ENDPOINT})'
    rf'(?:\s*[-–—]\s*({_DATE_ENDPOINT}))?'
    r'[\s)\].,;:]*$',
    re.IGNORECASE,
)
_LEADING_TYPE_RE = re.compile(r'^([^,:;–—-]{1,40})\s*[,:;–—-]\s+(.+)$')
_BRACKET_PAIRS = (('(', ')'), ('[', ']'))


class MembershipsRowShapeError(ValueError):
    """`_add_table_row` was handed more values than the table has columns.

    A caller/template programming error, never CV content: both call sites in
    `_fill_memberships` pass a two-element list, and the memberships table the
    section writes into has two columns on every path that reaches the writer
    (the WCM template's own table, or a fallback table the header guard already
    rejected unless it had at least two columns). Content decides what goes IN
    the two cells, never how many cells there are -- so this cannot abort a
    document over a bad CV, and failing loudly beats emitting a document that
    silently lost a column (#476 review).
    """


class MembershipFallback(NamedTuple):
    """What `_organization_fallback` recovered from an entry's raw text.

    A record rather than a bare tuple because the three strings are all
    optional, all interchangeable by type, and read at a call site that then
    merges each one INDIVIDUALLY with a structured field of the same name --
    `recovered[0]` at that call site would be a silent swap waiting to happen.
    """
    membership_type: str
    organization: str
    dates: str


def _classify_part(part: str) -> str:
    """'type', 'date' or 'organization' for one fragment of an entry.

    Mirrors `_parse_multi_membership_entry`'s own precedence exactly: a
    membership-type keyword in a short-enough part wins over a date shape,
    and anything that is neither is an organization.
    """
    if any(kw in part.lower() for kw in _MEMBERSHIP_TYPE_KEYWORDS) \
            and len(part.split()) <= _MEMBERSHIP_TYPE_MAX_WORDS:
        return 'type'
    if _DATE_PART_RE.match(part) or _DATE_PREFIX_RE.match(part):
        return 'date'
    return 'organization'


def _parts_look_like_memberships(parts: list[str]) -> bool:
    """True when a '|' split produced something shaped like membership records.

    '|' is not always structural: it survives extraction inside a single
    organization or source field too, and splitting there turns one membership
    into several (#476 review). A membership record is a membership TYPE, an
    ORGANIZATION and a DATE RANGE in some order, so the split is only accepted
    when the parts supply all three roles. Two halves of one organization name
    supply only the organization role and are left fused, which is what the
    renderer did before the split existed.
    """
    if len(parts) < 2:
        return False
    roles = {_classify_part(part) for part in parts}
    return roles == {'type', 'date', 'organization'}


def _entry_parts(text: str) -> list[str]:
    """Parts of a memberships entry (#476), scoped to exactly the
    newline-blind case: a text with no literal newline at all keeps
    `entry_lines`'s own single opaque part today, even when it is really a
    "Type1 | Org1 | Date1 | Type2 | Org2 | Date2" fused entry that
    `_parse_multi_membership_entry`'s own '|' handling (parsing/text.py:209)
    could otherwise see as more than one membership.

    A genuinely multi-line entry (entry_lines already returns >1 part) is
    returned UNCHANGED. Reading the farm's own newline-blind uids proved
    this matters: 2071_Zuschlag_Cv's "2009-2012\\t\\tStudent Osteopathic
    Surgical Association\\t\\t\\n\\n2009-2012\\t\\tFlorida Osteopathic Medical
    Association" already has 2 lines by newline alone, each carrying its own
    unsplit tabs; running the multi-membership parser on that produces two
    "organizations" that are really unparsed tab-laden blobs (date prefix and
    literal tab baked into the org cell, dates column empty) -- worse than
    today's single clean row. This fix is narrower than that entry's real
    bug (a genuine 2-record fusion the #221 recovery pass papers over with an
    Appendix bullet); it isn't newline-blind and isn't this issue's to fix.

    The split is also only accepted when the resulting parts actually parse as
    membership records -- see `_parts_look_like_memberships`. An entry whose
    single '|' is punctuation inside one organization or source field keeps its
    single opaque part rather than becoming two organizations.
    """
    lines = entry_lines(text)
    if len(lines) != 1:
        return lines
    parts = [p.strip() for p in lines[0].split('|') if p.strip()]
    if not _parts_look_like_memberships(parts):
        return lines
    return parts


def _split_date_range(dates: str) -> tuple[str, str]:
    """One raw membership date cell -> the (start, end) pair the formatter wants.

    `_parse_multi_membership_entry` returns a range already joined into one
    string; the single-membership path has the two halves as separate fields.
    Reducing the first shape to the second is what lets both reach the same
    `format_date_range` call.
    """
    if not dates:
        return '', ''
    halves = _DATE_RANGE_SPLIT_RE.split(dates.strip(), maxsplit=1)
    return halves[0].strip(), (halves[1].strip() if len(halves) > 1 else '')


def _membership_dates_cell(start_date: str, end_date: str) -> str:
    """The ONE place a section I date cell is formatted (#476 review).

    Both the single- and multi-membership paths end here, so equivalent
    memberships cannot render different date formats depending on which branch
    the entry took.
    """
    if not (start_date or end_date):
        return ''
    return format_date_range(start_date, end_date, _MEMBERSHIPS_TAXONOMY_CODE)


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens, punctuation dropped."""
    return [token for token in _TOKEN_SPLIT_RE.split(text.lower()) if token]


def _type_already_named(membership_type: str, organization: str) -> bool:
    """True when the organization name already contains the membership type.

    Phrase containment over WORDS, not characters (#476 review): the type's
    tokens must appear as a contiguous run of the organization's tokens. A
    substring test suppressed "Member" from any organization containing those
    six letters inside a longer word, and "Fellow" from anything containing
    "fellowship".
    """
    type_tokens = _tokens(membership_type)
    if not type_tokens:
        return True
    org_tokens = _tokens(organization)
    span = len(type_tokens)
    return any(org_tokens[i:i + span] == type_tokens
               for i in range(len(org_tokens) - span + 1))


def _organization_cell(membership_type: str, organization: str) -> str:
    """The organization column's text: "Type, Organization" or just the name."""
    if membership_type and not _type_already_named(membership_type, organization):
        return f"{membership_type}, {organization}"
    return organization


def _normalize_header_cell(cell_text: str) -> str:
    """Case- and whitespace-fold one table header cell for matching."""
    return _HEADER_WHITESPACE_RE.sub(' ', str(cell_text or '')).strip().lower()


def _is_date_header_cell(cell_text: str) -> bool:
    """True when a header cell names a date column.

    Normalized, so "DATE", "Dates", "Date  Awarded" and "Date (yyyy-yyyy)" all
    match where a literal "Date" substring test accepted only the last two
    (#476 review). Still a whole-word test, so "Update" is not a date column.
    """
    return bool(_DATE_HEADER_RE.search(_normalize_header_cell(cell_text)))


def _truncate_on_word_boundary(text: str, limit: int) -> str:
    """`text` cut to `limit` characters at the last whole word that fits."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(' ')
    return (cut[:space] if space > 0 else cut).rstrip()


def _brackets_balanced(text: str) -> bool:
    """True when every bracket `text` opens it also closes."""
    return all(text.count(opener) == text.count(closer)
               for opener, closer in _BRACKET_PAIRS)


def _first_field(fields: dict, aliases: tuple[str, ...]) -> str:
    """The first non-empty value `fields` holds under any of `aliases`."""
    for key in aliases:
        value = str(fields.get(key) or '').strip()
        if value:
            return value
    return ''


def _structured_dates(fields: dict) -> str:
    """The entry's own `start_date`/`end_date` as one raw range string.

    Only the no-loss check reads this. The caller already prefers these fields
    over anything the recovery lifts out of the text, so the check has to count
    them as material that reaches the date column -- otherwise an entry whose
    dates live in the fields rather than in a fragment the scanner recognizes
    is refused for "losing" words the row does in fact render.
    """
    start = str(fields.get('start_date') or '').strip()
    end = str(fields.get('end_date') or '').strip()
    if start and end:
        return f"{start}-{end}"
    return start or end


def _recovery_covers_text(text: str, recovered: MembershipFallback) -> bool:
    """True when every word of `text` still reaches a cell of the rendered row.

    The recovery is allowed to MOVE words -- a date into the date column, a
    membership type into the prefix `_organization_cell` writes back -- but it
    is not allowed to lose any. Word-level and set-based, so re-ordering,
    punctuation, tabs and a repeated organization name all read as covered.

    Dates are counted at the material that reaches the date column, not at the
    precision the column prints: section I's date format is 'yyyy', so a
    "1/1997" in the source renders as "1997" whether or not this recovery ever
    ran. That narrowing is the column's own long-standing policy, applied to
    every section I entry; this check is about the recovery, so it must not
    read a policy loss as a recovery loss.
    """
    rendered = set(_tokens(' '.join((recovered.membership_type,
                                     recovered.organization,
                                     recovered.dates))))
    return all(token in rendered for token in _tokens(text))


def _split_trailing_dates(fragment: str) -> tuple[str, str]:
    """Lift a trailing date or date range off a fragment.

    Returns (fragment without the dates, the dates). The remainder is empty
    for a fragment that is nothing BUT a date -- the caller reads that as
    "this fragment is the date column, not the organization" and keeps
    looking.

    A written-out month counts as part of the date. Reading only the year left
    the month behind as the "organization", which on the farm's 6NGAYQ turned
    an entry whose whole text is "February 2018 - Present" into an
    organization cell reading "February": a strictly shortened prefix of what
    the section rendered before, and the same class of loss the raw-text
    fallback was replaced to stop.
    """
    match = _TRAILING_DATES_RE.search(fragment)
    if not match:
        return fragment, ''
    remainder = fragment[:match.start()].strip(' ,;:-–—([')
    if remainder and _brackets_balanced(fragment) and not _brackets_balanced(remainder):
        # The date sat inside a bracketed aside ("... (elected Fellow, 1980)")
        # and lifting it would leave the organization holding a bracket it
        # never closes. Leave the fragment whole instead.
        return fragment, ''
    start, end = match.group(1), match.group(2) or ''
    return remainder, (f"{start}-{end}" if end else start)


def _split_leading_membership_type(fragment: str) -> tuple[str, str]:
    """Separate a leading "Member, " / "Elected Fellow - " prefix off a fragment.

    Returns ('', fragment) unless the prefix is short enough and keyword-shaped
    enough to be a membership type by the same rule `_classify_part` uses, so
    "Fellow of the American College of Surgeons" -- which carries no separator
    -- stays intact.
    """
    match = _LEADING_TYPE_RE.match(fragment)
    if not match:
        return '', fragment
    prefix, rest = match.group(1).strip(), match.group(2).strip()
    if not rest or _classify_part(prefix) != 'type':
        return '', fragment
    return prefix, rest


def _organization_fallback(text: str, fields: dict) -> MembershipFallback:
    """Recover an organization for an entry whose `organization` field is empty.

    Replaces `original_text[:150]`, which rendered membership type, dates and
    any unrelated trailing source text into the organization cell, truncated
    mid-word (#476 review). Order of preference:

    1. a structured field that names the same thing under another key;
    2. the entry's first fragment (newline, tab or pipe) that is neither purely
       a date nor purely a membership type, with any trailing date range lifted
       into the date column and a leading membership-type prefix separated out
       so the normal `_organization_cell` path prefixes it;
    3. that fragment truncated at a word boundary if it is still over-long.

    The organization and the membership type are chosen INDEPENDENTLY, each
    preferring its own structured alias and each falling back to the fragment
    scan. Resolving them together -- returning early as soon as an alias named
    the organization -- is what lost "Faculty Associate" off 2082_Dr_Scot's
    "2016-present\\tFaculty Associate. Center for Southeast Asian Studies":
    `institution` named the organization, so the `role` beside it was never
    read and the type reached no cell at all.

    Skipping date-only and type-only fragments matters: extraction flattens a
    two-column source table into "1999\tSome Society..." and a pipe-joined
    record into "Fellow | Some Society | 1/1997-present", and taking fragment
    zero blindly would render the year, or the word "Fellow", as the
    organization. Every fragment is still scanned for a date even after the
    organization is found, so the date in a trailing fragment is not lost.

    The date it lifts out reaches the date column through the same
    `_membership_dates_cell` call as every other date, so a lifted start with
    no end renders "1999-Present" exactly as a structured `start_date` with no
    `end_date` already does.

    The organization it returns is empty only when the entry carries no text
    at all, so a non-blank entry cannot become a blank row through this path.

    EVERY recovery is then checked against one invariant, the alias-supplied
    ones included: the row may MOVE a word out of the organization cell, never
    lose it. `_recovery_covers_text` re-reads the entry's own words and
    requires each one to still land in the organization, the membership type
    or the date. A recovery that fails goes back to the whole raw text --
    exactly the cell the section rendered before this extractor existed, so a
    fragment the segmentation misread, or an alias field that names less than
    the entry does, can only cost the row its improvement, never any of its
    content. It fires on the farm's tab-flattened two-column entries whose
    date half is written in a shape the date test does not read ("2003-",
    "2018-Pres", "2008-12"): the date half then reads as the organization and
    the real name, sitting in the next fragment, would have been dropped.

    Round 2 exempted the structured-alias branch from that check, on the
    argument that an alias field is an extraction result about the whole entry
    rather than a re-segmentation of its text. The exemption is gone, because
    that argument is wrong in the one way that matters: an alias names ONE
    field, so it can be right about the organization and still leave the rest
    of the entry unaccounted for. Nothing about the branch is exempt now --
    what makes an alias-supplied row pass is that the type alias and the
    entry's own date fields cover the words the organization alias does not.
    """
    fragments = [f.strip() for f in _FRAGMENT_SPLIT_RE.split(str(text or '')) if f.strip()]

    dates, membership_type, organization = '', '', ''
    for fragment in fragments:
        remainder, fragment_dates = _split_trailing_dates(fragment)
        if fragment_dates and not dates:
            dates = fragment_dates
        if not remainder:
            continue  # the whole fragment was a date: it belongs in the date column
        if _classify_part(remainder) == 'type':
            membership_type = membership_type or remainder
            continue  # the whole fragment was a membership type
        if organization:
            continue  # already found; keep scanning only to collect dates
        prefix, rest = _split_leading_membership_type(remainder)
        membership_type = membership_type or prefix
        organization = rest

    # A structured field beats the scan for the value it names -- and only for
    # that value.
    membership_type = _first_field(fields, _MEMBERSHIP_TYPE_FIELD_ALIASES) or membership_type
    organization = _first_field(fields, _ORGANIZATION_FIELD_ALIASES) or organization
    dates = dates or _structured_dates(fields)

    if not organization:
        # Every fragment was a date or a bare membership type, and no field
        # names one either. Keep the entry's own first fragment rather than
        # render a blank cell.
        membership_type, organization = '', (fragments[0] if fragments else '')

    recovered = MembershipFallback(membership_type, organization, dates)
    if not _recovery_covers_text(text, recovered):
        logger.warning(
            "memberships: recovering an organization from raw text would have "
            "dropped part of the entry; rendering the raw text instead"
        )
        recovered = MembershipFallback('', str(text or '').strip(), '')

    return MembershipFallback(
        recovered.membership_type,
        _truncate_on_word_boundary(recovered.organization,
                                   _ORGANIZATION_FALLBACK_MAX_CHARS),
        recovered.dates,
    )


def _single_membership_row(fields: dict, original_text: str) -> tuple[str, str, bool]:
    """The (organization cell, dates cell, fallback fired) for one membership.

    Module-level rather than a method: it needs no generator state, and every
    name added to a section mixin is a name that can shadow another mixin's
    (the `_add_table_row` collision).
    """
    organization = str(fields.get('organization') or '').strip()
    membership_type = str(fields.get('membership_type') or '').strip()
    start_date = str(fields.get('start_date') or '').strip()
    end_date = str(fields.get('end_date') or '').strip()

    fallback_fired = False
    if not organization:
        fallback_fired = True
        recovered = _organization_fallback(original_text, fields)
        organization = recovered.organization
        membership_type = membership_type or recovered.membership_type
        if not (start_date or end_date):
            start_date, end_date = _split_date_range(recovered.dates)

    return (_organization_cell(membership_type, organization),
            _membership_dates_cell(start_date, end_date),
            fallback_fired)


class MembershipsSection:
    """Section I writers, mixed into `WCMTemplateGenerator`."""

    def _fill_memberships(self, entries: list[dict]) -> None:
        """Fill I. PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS section.

        Entries have fields: organization, membership_type, start_date, end_date
        Uses table with columns: Organization, Date (yyyy-yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Memberships ({len(entries)} entries)...")

        # Find the MEMBERSHIPS section
        memberships_idx = self._find_paragraph_with_text("PROFESSIONAL ORGANIZATIONS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("SOCIETY MEMBERSHIPS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("MEMBERSHIPS")
        if memberships_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(memberships_idx)
        if not table:
            # Fall back to finding table with "Organization" header
            table = self._find_table_with_cell_text("Organization")
            if table and (
                not table.rows
                or len(table.rows[0].cells) < 2
                or not _is_date_header_cell(table.rows[0].cells[1].text)
            ):
                table = None  # Wrong table

        if not table:
            if self.verbose:
                print("  Warning: Could not find memberships table")
            return

        # Clear existing data rows
        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries
            if _is_table_header_entry(original_text, ['organization', 'membership', 'society', 'date', 'member']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Check if this entry contains multiple memberships (newline- or,
            # for a fully blind entry, '|'-separated -- see _entry_parts,
            # #476). Pattern: "Member\nElected Member | Org1\nOrg2 |
            # date1\ndate2", or fully blind: "Type1 | Org1 | Date1 | Type2 |
            # Org2 | Date2".
            lines = entry_lines(original_text)
            parts = _entry_parts(original_text)

            # Detect multi-membership pattern: multiple organization names or
            # membership types. `_parse_multi_membership_entry` already does
            # its own '|' splitting and keyword/date classification per part.
            #
            # Two gates, and which one applies depends on whether this entry
            # is one #476 newly splits:
            #
            #  - already multi-line (`len(lines) > 2`): unchanged from before
            #    #476 -- any parse result at all renders, including a
            #    single-membership one. `parts` IS `lines` here, so this is
            #    byte-for-byte the old `if len(lines) > 2: ... if memberships:`
            #    behaviour, deliberately preserved rather than folded into
            #    the stricter gate below.
            #  - newly split by '|' out of one blind line: gate on the PARSED
            #    COUNT, not just the raw part count. A single membership whose
            #    fields happen to split into exactly three parts ("Fellow |
            #    American Academy of Pediatrics | 1/1997-present", farm
            #    entries on 1FRABQ and others) crosses the >2 threshold but
            #    must still resolve to one membership, not be misread as
            #    several.
            memberships = _parse_multi_membership_entry(parts) if len(parts) > 2 else []
            if len(memberships) > 1 or (memberships and len(lines) > 2):
                for mem_type, org, dates in memberships:
                    # `_add_table_row` owns stats['entries_inserted'] -- do not
                    # increment it here as well (#476 review).
                    self._add_table_row(
                        table,
                        [_organization_cell(mem_type, org),
                         _membership_dates_cell(*_split_date_range(dates))],
                        entry=entry,
                    )
                continue

            # Single membership - use extracted fields, or recover from the
            # raw text when the organization field is empty.
            org_text, date_str, fallback_fired = _single_membership_row(fields, original_text)

            if not (org_text.strip() or date_str.strip()):
                # Neither column has content: an empty, whitespace-only or
                # separator-only source record. Rendering it would add a blank
                # row to the document, so it is dropped -- counted and logged
                # rather than dropped silently.
                self.stats['membership_entries_blank'] = \
                    self.stats.get('membership_entries_blank', 0) + 1
                logger.warning(
                    "memberships: entry at %s has neither an organization nor a "
                    "date; no row rendered",
                    entry.get('element_idx_start', '?'),
                )
                continue

            if fallback_fired:
                self.stats['membership_organization_fallbacks'] = \
                    self.stats.get('membership_organization_fallbacks', 0) + 1
                logger.warning(
                    "memberships: entry at %s has no 'organization' field; the "
                    "organization was recovered from its raw text",
                    entry.get('element_idx_start', '?'),
                )

            # Add row to table
            self._add_table_row(table, [org_text, date_str], entry=entry)

    def _add_table_row(self, table: Table, data: list[str], is_header: bool = False,
                       entry: dict | None = None) -> None:
        """Add a row to a table with proper formatting.

        Owns `stats['entries_inserted']`: incremented here, once per row
        actually written, and never by the callers as well. Both layers used to
        increment it, so one rendered membership reported two inserted entries
        (#476 review).

        Args:
            table: The table to add to
            data: List of cell values
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline

        Raises:
            MembershipsRowShapeError: `data` is longer than the table is wide.
        """
        if not table:
            return

        # Validate before adding the row so a rejected call leaves no stray
        # empty row behind. `add_row()` creates exactly one cell per grid
        # column, so this count is the added row's cell count.
        columns = len(table.columns)
        if len(data) > columns:
            raise MembershipsRowShapeError(
                f"memberships row writer got {len(data)} value(s) for a table with "
                f"{columns} column(s); the surplus value(s) would be dropped silently"
            )

        row = table.add_row()
        first_cell_para = None
        for i, value in enumerate(data):
            cell = row.cells[i]
            cell.text = str(value) if value else ""
            # Set vertical alignment to center (middle)
            _set_cell_vertical_alignment(cell, 'center')
            for para in cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    # Always 11pt Arial, bold for headers
                    _set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1
