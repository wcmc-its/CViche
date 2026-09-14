"""Section Q: extramural professional responsibilities (#398).

The one section in this package that is really four, and they are kept together
because they are not independent: `_fill_service` is a router, and the routing
is the section's actual logic.

    _fill_service_boards          Q2       Service on Boards -- Regional /
                                           National / International, chosen by
                                           comparing the entry's location
                                           against the CV owner's
    _fill_journal_reviewing       Q4D      Journal / ad hoc reviewing
    _fill_other_service           Q1, Q3,  bullet lists, and it hands Q1 on to
                                  Q4, Q4A- _fill_extramural_leadership
                                  Q4C
    _fill_extramural_leadership   Q1       Leadership in Extramural Organizations

`_fill_service` reroutes before it dispatches: a Q2 entry whose text is really
journal reviewing is moved to Q4D, and a multi-line Q2 entry mixing both is
split line by line. Separating the writers into four modules would put the
dispatcher in one file and the thing it corrects in another, which is the split
that would have to be undone first to understand either.

`_add_extramural_row` and `_parse_extramural_leadership_lines` are shared by two
of the four writers and by nothing outside the section.
"""
import logging
import re
from collections.abc import Sequence
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..sorting import sort_entries_reverse_chronological
from ..normalization import _cell_text, _squash

logger = logging.getLogger(__name__)
from unified_pipeline.core.render_check import entry_lines

# Taxonomy codes for Section Q: EXTRAMURAL PROFESSIONAL RESPONSIBILITIES (docs/CODING_STANDARDS.md §8.2).
SERVICE_TAXONOMY_CODES = ('Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D')  # every code this section routes
BOARD_SERVICE_CODE = 'Q2'               # Service on Boards and/or Committees
JOURNAL_REVIEWING_CODE = 'Q4D'          # Journal / ad hoc reviewing
LEADERSHIP_TAXONOMY_CODE = 'Q1'         # Leadership in Extramural Organizations
GRANT_REVIEWING_CODE = 'Q3'             # Grant Reviewing / Study Sections
EDITORIAL_BOARD_CODES = ('Q4B', 'Q4C')  # Editorial Board Membership roles

# Q3/Q4/Q4A/Q4B/Q4C -> (WCM template section display name, header search-text candidates)
OTHER_SERVICE_SECTION_ROUTING = {
    GRANT_REVIEWING_CODE: ('Grant Reviewing', ['Grant Reviewing', 'Study Sections']),
    'Q4': ('Professional Service', ['EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', 'Leadership in Extramural']),
    'Q4A': ('Editor/Co-Editor', ['Editor/Co-Editor', 'Journals/Textbooks/Books']),
    'Q4B': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
    'Q4C': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
}

# Q2 -> Q4D reroute vocabulary, split at the natural seam between specialty
# terms and role phrases rather than a fixed-width slice of one combined
# list (#573).
JOURNAL_SPECIALTY_KEYWORDS = (
    'journal', 'j.', 'j ', 'pediatrics', 'lancet', 'jama',
    'perinatology', 'neonatology', 'oncology', 'cardiology', 'neurology',
)
JOURNAL_ROLE_PHRASES = ('editorial board', 'ad hoc reviewer', 'manuscript review')
REVIEWER_PATTERNS = (
    'abstract reviewer', 'reviewer for', 'manuscript reviewer',
    'peer reviewer', 'ad hoc reviewer',
)
BOARD_KEYWORDS = (
    'committee', 'board member', 'panel member', 'council', 'task force',
    'working group', 'planning committee', 'advisory', 'moderator',
)

# Role indicators used by `_is_known_org_line`'s veto and the sibling
# `is_role` check in `_parse_extramural_leadership_lines`, both matched with
# `_matches_word_start` (below), not `_matches_bounded`. Promoted from a
# local list literal (duplicated ad hoc between `_is_known_org_line`'s two
# call sites) to a named module constant so this round's fix is directly
# testable, matching REVIEWER_PATTERNS/BOARD_KEYWORDS above.
#
# These are STEMS, not whole words: `_matches_word_start` matches each entry
# plus any `_ROLE_STEM_SUFFIXES` form (below), so adding a stem here adds
# every one of its suffix forms at once, not just the bare word (#658 round
# 4 review -- a plain list literal read as exact keywords, which this no
# longer is).
EXTRAMURAL_ROLE_KEYWORDS = (
    'member', 'chair', 'reviewer', 'liaison', 'mentor', 'committee',
    'board', 'council', 'advisor', 'director', 'leader', 'representative',
    # These three are listed as their own stems because `_matches_word_start`
    # cannot reach them any other way. 'subcommittee' does not start with
    # "committee" -- the matcher anchors on the word's START, so the entry
    # above cannot reach it; real committee role lines use it ("AHA Hospital
    # Accreditation Stroke Certification Subcommittee", "LCME Self Study
    # Subcommittee: ...") -- 50 line occurrences over 14 corpus UIDs (#658
    # round 3 probe). 'advisory' and 'directorate' are DERIVED forms
    # (`advisor` + `y`, `director` + `ate`) that `_ROLE_STEM_SUFFIXES`
    # cannot reach either: admitting 'y'/'ate' as suffixes there would also
    # turn "director" into the unrelated word "directory" (#658 round 4
    # review, T1). So each derived word the suffix rule cannot reach
    # without that side effect is listed here as its own stem instead:
    # advisory (187 corpus lines), directorate (2 lines) (#658 round 4
    # probe, 15,079 entry lines).
    'subcommittee', 'advisory', 'directorate',
)

# The inflected and derived forms a role stem may take, and nothing else:
# every form observed on the 66-CV corpus (#658 round 4 probe, 15,079 entry
# lines) is here, so "boardwalk", "leaderboard" or "directory" cannot match
# while "chairman", "directors", "mentorship" still do. 'y' and 'ate' are
# deliberately absent: admitting them would reach "advisory" and
# "directorate" but would also let "director" + 'y' match the unrelated
# word "directory" -- so those two derived forms are instead listed as
# their own EXTRAMURAL_ROLE_KEYWORDS entries above (#658 round 4 review,
# T1).
_ROLE_STEM_SUFFIXES = ('s', 'es', 'ed', 'ing', 'ship', 'ships', 'man', 'men',
                       'woman', 'women', 'person', 'persons', 'people',
                       'or', 'ors', 'lor', 'lors')


def _matches_bounded(text_lower: str, keywords: Sequence[str]) -> bool:
    """True when any keyword/phrase in `keywords` matches `text_lower` as a
    whole word or phrase, not merely as a run of characters inside a larger
    word (#658).

    Plain substring containment (`kw in text_lower`) let "reviewer education
    committee" trip both REVIEWER_PATTERNS and BOARD_KEYWORDS on fragments
    that happened to co-occur, and would match a keyword embedded in an
    unrelated longer word. `\\b` around each escaped keyword/phrase fixes
    both without changing which whole-word/whole-phrase hits count.
    """
    return any(re.search(rf'\b{re.escape(kw)}\b', text_lower) for kw in keywords)


def _matches_word_start(text_lower: str, keywords: Sequence[str]) -> bool:
    """True when `text_lower` contains one of `keywords` as a stem, at a
    word start, optionally followed by one of `_ROLE_STEM_SUFFIXES` and
    nothing else before the next word boundary (#658 rounds 2-4).

    Used only for `EXTRAMURAL_ROLE_KEYWORDS`, not the other keyword lists
    `_matches_bounded` still serves. `_matches_bounded`'s whole-word
    `\\b...\\b` fixed keyword-embedded-in-an-unrelated-word (#658), but it
    also stopped matching `EXTRAMURAL_ROLE_KEYWORDS`' own inflected and
    derived forms -- "member" no longer matched "members"/"membership",
    "chair" no longer matched "chairman"/"chaired", "director" no longer
    matched "directors"/"directorship", "mentor" no longer matched
    "mentoring"/"mentorship", "council" no longer matched "councilor",
    "leader" no longer matched "leaders"/"leadership". A differential probe
    over every entry line in the corpus's stage-5d artifacts found the
    whole-word bound dropped these forms on real leadership-table role
    lines ("Program Chairman, ...", "Association of Directors of Medical
    Student Education...", "Developing Leaders in Pediatric..."),
    misreading them as organization lines.

    The forms are an explicit finite list (`_ROLE_STEM_SUFFIXES`), not an
    unbounded prefix match. An earlier round anchored only the word's start
    with no right bound at all, on the reasoning that English
    inflection/derivation overwhelmingly adds a suffix rather than a
    prefix -- true, but not sufficient: unbounded matching also let
    "boardwalk", "leaderboard" and "directory" read as role indicators,
    which they are not (#658 round 4 review). Corpus-deriving the suffix
    list instead of hand-guessing it matters because a hand list keeps
    missing real forms -- but not every real form belongs in
    `_ROLE_STEM_SUFFIXES` itself: "advisory" (`advisor` + `y`, 187 corpus
    occurrences) and "directorate" (`director` + `ate`, 2 occurrences) are
    both real leadership-table role words a round-3-style hand list would
    have missed, yet admitting 'y' and 'ate' as suffixes here would also
    let "director" + 'y' match "directory", an unrelated word with no
    corpus role reading. Both are instead listed as their own
    `EXTRAMURAL_ROLE_KEYWORDS` stems (see that constant's comment), so they
    still match without opening the suffix list to that collision.

    The left boundary, `(?<![^\\W\\d_])`, means "not preceded by a Unicode
    letter" -- equivalently, "not preceded by a `\\w` character that is not
    a digit or underscore". `\\b` sits between a word and a NON-word
    character, so it treats a digit as part of the word and refuses to
    start one: "2010-2012" is followed directly by "director" in real
    stage-4 extractions ("2010-2012Director, Cardiac Prep and Recovery
    Unit") where the source docx had the date and title in adjacent cells,
    and a `\\b` matcher scored those lines as organizations. A digit -- and
    any punctuation -- is a legitimate word start for this classifier's
    purposes; only a preceding LETTER means the keyword is buried mid-word.
    Being Unicode-aware, not the ASCII-only `[A-Za-z]` an earlier round
    used, matters here specifically: `[A-Za-z]` does not recognize "e" in
    "café" as a letter that blocks a match, so "cafémember" would
    have matched "member" as if at a word start; `(?<![^\\W\\d_])` correctly
    treats "é" as a letter and blocks it. That is the one case still
    rejected, deliberately: "eMentorship" contains "mentor" behind a
    letter, so it does not match (an accepted mis-file, see the PR body).

    Concatenation where the preceding character is itself a letter is not
    recoverable here and is accepted: four lines on one corpus CV glue a
    keyword onto the word "present" ("2014-presentReviewer, ...") and stay
    unmatched, since "t" is a letter. Stripping trailing date words before
    matching was considered and rejected as a date-parsing responsibility
    that does not belong in a keyword matcher; none of these lines reach
    `_parse_extramural_leadership_lines` in the corpus.

    This does not regress relative to `_matches_bounded`: every whole-word
    match is still a match here too, since the bare stem followed by
    nothing (the suffix group is optional) and then a word boundary is
    exactly what `_matches_bounded` requires -- confirmed by a differential
    probe over the corpus finding 0 disagreements against the
    previously-shipped unbounded-right-edge matcher.
    """
    suffix_alternation = '|'.join(_ROLE_STEM_SUFFIXES)
    return any(
        re.search(
            rf'(?<![^\W\d_]){re.escape(kw)}(?:{suffix_alternation})?\b',
            text_lower,
        )
        for kw in keywords
    )


# A multi-line Q2 entry's trailing date-only lines (e.g. "2014, 2017-2020")
# describe the line before them; nothing else should be treated as a
# continuation (#573 review).
_DATE_ONLY_LINE_RE = re.compile(
    r'^\d{4}(?:\s*[-–,]\s*(?:\d{4}|present|current))*\s*$', re.IGNORECASE)

# Role titles recognized when Stage 4 merges a committee name into the role
# field (e.g. role="Chair, Ultrasound Committee") and it needs splitting
# back apart. Used only by `_fill_service_boards`. Single source of truth
# for base titles -- `_is_board_role_title` below extends it to qualified
# and chair-variant phrasing without duplicating any of these strings.
BOARD_ROLE_TITLES = (
    'chair', 'co-chair', 'deputy chair', 'vice chair', 'member',
    'secretary', 'treasurer', 'president', 'vice president', 'director',
    'advisor', 'liaison', 'representative', 'reviewer', 'editor', 'delegate',
)

# "Chair" variants BOARD_ROLE_TITLES spells only as "chair"/"co-chair"/
# "deputy chair"/"vice chair"; recognized directly so a Stage-4 role of
# "Chairperson, Ultrasound Committee" still splits (#624 review).
_CHAIR_TITLE_VARIANTS = ('chairperson', 'chairman', 'chairwoman')

# Qualifiers Stage 4 sometimes prefixes onto a board role title (e.g.
# role="Past President, XYZ Committee"). Recognized only as an exact
# "<qualifier> <base title>" two-word phrase -- whole-phrase matching, not
# a startswith/substring heuristic, so a phrase like "membership
# committee" that merely shares a substring with a base title is never
# mistaken for one (#624 review).
_ROLE_QUALIFIERS = (
    'past', 'interim', 'acting', 'honorary', 'deputy', 'vice', 'co',
    'associate', 'assistant',
)


def _is_board_role_title(role_word: str) -> bool:
    """True when `role_word` (already stripped/lowercased) is a board role
    title Stage 4 may have merged a committee name onto.

    Recognizes an exact `BOARD_ROLE_TITLES` entry, a chair variant
    (`_CHAIR_TITLE_VARIANTS`), or an exact `<qualifier> <base title>`
    two-word phrase built from `_ROLE_QUALIFIERS` -- whole-phrase equality
    checks only, never startswith/substring, so "membership committee" is
    not mistaken for a role title (#624 review).
    """
    if role_word in BOARD_ROLE_TITLES or role_word in _CHAIR_TITLE_VARIANTS:
        return True
    parts = role_word.split(' ', 1)
    if len(parts) == 2:
        qualifier, base = parts
        if qualifier in _ROLE_QUALIFIERS and (
                base in BOARD_ROLE_TITLES or base in _CHAIR_TITLE_VARIANTS):
            return True
    return False


def _split_q2_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split one multi-line Q2 entry's lines into journal and board lines.

    A bare date-only continuation line extends the *previous* line rather
    than starting a new board-defaulted entry, so a stray date does not end
    up in the board entry when the line it describes was rerouted to
    journal reviewing. Gated on actually looking like a date: an
    unclassified content line (neither pattern list matches it) still
    starts its own board_lines item rather than being silently absorbed
    into whatever line happened to precede it (#573 review).
    """
    journal_lines: list[str] = []
    board_lines: list[str] = []
    last_group: list[str] | None = None

    for line in lines:
        line_lower = line.lower()
        is_reviewer_line = _matches_bounded(line_lower, REVIEWER_PATTERNS)
        is_board_line = _matches_bounded(line_lower, BOARD_KEYWORDS)

        if (not is_reviewer_line and not is_board_line and last_group
                and _DATE_ONLY_LINE_RE.match(line)):
            last_group[-1] = f"{last_group[-1]} {line}"
            continue

        if is_reviewer_line and not is_board_line:
            journal_lines.append(line)
            last_group = journal_lines
        else:
            board_lines.append(line)
            last_group = board_lines

    return journal_lines, board_lines


def _is_q2_journal_reviewer(text_lower: str, role: str, committee: str,
                            org: str) -> bool:
    """True when a single-line Q2 entry is really journal reviewing.

    BOARD_KEYWORDS vetoes the reroute even when a journal signal also
    matches, so an entry naming both a journal and a committee stays a
    board entry.
    """
    is_journal_reviewer = (
        (role == 'reviewer' and any(kw in text_lower for kw in JOURNAL_SPECIALTY_KEYWORDS)) or
        any(kw in text_lower for kw in JOURNAL_ROLE_PHRASES) or
        _matches_bounded(text_lower, REVIEWER_PATTERNS) or
        (role == 'reviewer' and 'j ' in committee) or
        (role == 'reviewer' and 'journal' in org)
    )
    is_board_entry = _matches_bounded(text_lower, BOARD_KEYWORDS)
    return is_journal_reviewer and not is_board_entry


def _route_q2_entries(q2_entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Reroute Q2 entries that are really journal reviewing to Q4D.

    Handles misclassified single-line entries (a journal "Reviewer" role
    coded as Q2) and multi-line entries mixing both activities, split line
    by line via `_split_q2_lines`. Takes plain entry dicts, no docx object
    and no `self` access, so the routing decision is testable without a
    document (#624 review).
    """
    rerouted_to_journal: list[dict] = []
    actual_board_entries: list[dict] = []

    for entry in q2_entries:
        text = entry.get('text', '')
        text_lower = text.lower()
        fields = entry.get('extracted_fields', {}) or {}
        # #812 round 2: a structured (list/dict) committee_name/role/organization
        # raised AttributeError on `.lower()` here, upstream of every
        # `.text =` site this ticket otherwise fixed -- coerce first so the
        # routing check is always given a string (#812 finding 3).
        role = _cell_text(fields.get('role', '')).lower()
        committee = _cell_text(fields.get('committee_name', '')).lower()
        org = _cell_text(fields.get('organization', '')).lower()

        lines = entry_lines(text)
        if len(lines) > 1:
            journal_lines, board_lines = _split_q2_lines(lines)

            # Each journal line becomes its own synthetic entry, carrying
            # forward only the parent entry's date fields, not its identity
            # fields (organization/committee_name/role/journal_name) -- a
            # fused multi-line entry can mix several unrelated activities
            # under ONE set of extracted_fields (corpus CV HU4DXA), so an
            # identity field belongs only to the line it was extracted
            # from. _fill_journal_reviewing already parses a missing
            # journal name from each line's own raw text.
            date_fields = {k: v for k, v in fields.items()
                           if k in ('start_date', 'end_date', 'year')}
            for jline in journal_lines:
                rerouted_to_journal.append({
                    'text': jline,
                    'taxonomy_code': 'Q4D',
                    'rerouted_from_q2': True,
                    'extracted_fields': dict(date_fields),
                })

            # The remaining lines become a board entry, as a new dict
            # rather than a mutation of the shared original -- the same
            # entry object also lives in the flattened all_entries list the
            # orchestrator builds from entries_by_code (#573 review).
            if board_lines:
                actual_board_entries.append(
                    {**entry, 'text': '\n'.join(board_lines)})
        else:
            if _is_q2_journal_reviewer(text_lower, role, committee, org):
                # As a copy, not a mutation of the shared original.
                rerouted_to_journal.append(
                    {**entry, 'taxonomy_code': 'Q4D', 'rerouted_from_q2': True})
            else:
                actual_board_entries.append(entry)

    return rerouted_to_journal, actual_board_entries


# Specific, multi-word organization names/acronyms used by
# `_parse_extramural_leadership_lines`. Matched as a plain substring
# anywhere in the line -- safe because each is specific enough that no
# plausible role phrase contains it.
_KNOWN_ORG_NAMES = (
    'association of pediatric program directors', 'appd',
    'american academy of pediatrics', 'aap',
    'academic pediatric association', 'apa',
    'pediatric academic society', 'pas',
    'national board of medical examiners', 'nbme',
    'american medical association', 'ama',
    'american board of pediatrics', 'abp',
    'acgme', 'lenox hill',
)

# Generic organization terms that also occur inside role phrases ("Board
# Member", "Committee Chair"). Matched as a whole word/phrase, and only
# when the line does not also carry role vocabulary -- otherwise a role
# line that names its own kind of body ("Board Member") reads as an
# organization instead of the role it is (#624 review).
_GENERIC_ORG_TERMS = (
    'american college', 'society', 'association', 'academy', 'board',
    'institute',
)


def _is_known_org_line(line_lower: str, role_keywords: list[str]) -> bool:
    """True when a leadership-table line names a known organization.

    Two tiers: a `_KNOWN_ORG_NAMES` entry matches unconditionally as a
    substring, since it is specific enough not to collide with role
    phrasing. A `_GENERIC_ORG_TERMS` entry matches only as a whole
    word/phrase, and is vetoed entirely when the line also matches
    `role_keywords` -- so "Board Member" and "Member, Board of Directors"
    classify as role lines, not organizations, while "American College of
    Cardiology" and "Society of Critical Care Medicine" still classify as
    organizations (#624 review).

    The veto uses `_matches_word_start`, not `_matches_bounded`: see that
    function's docstring for why (#658 round 2) -- in short, role_keywords'
    base forms need to recognize their own inflected/derived forms
    ("members", "chairman"/"chaired", "directors"/"directorship",
    "mentoring"/"mentorship", "councilor", "leaders"/"leadership"), which a
    strict whole-word match otherwise misses.
    """
    if any(name in line_lower for name in _KNOWN_ORG_NAMES):
        return True
    if _matches_word_start(line_lower, role_keywords):
        return False
    return any(
        re.search(rf'\b{re.escape(term)}\b', line_lower)
        for term in _GENERIC_ORG_TERMS
    )


def _journal_name_cell_text(value: object, taxonomy_code: str) -> str:
    """Coerce a stage-4 `journal_name` value to plain cell text, formatting a
    list of per-journal records with their dates rather than dropping them.

    A Q4C entry that lists several editorial-board memberships in one line
    ("Editorial Board Member for: <journal>, 2003 - 2004; <journal>, 2014 -
    Present; ...") can come back from stage 4 as a list of `{"name": ...,
    "start_date": ..., "end_date": ...}` dicts instead of a string (#812,
    web204's 2,938-entry CV aborted stage 6 entirely on this shape).
    `_cell_text` (the generic list/dict -> text coercer, aliased from
    `_committee_cell_text`) already makes any shape here safe to assign to a
    cell, but it only ever returns the name-like value -- it has no notion of
    a date -- so routing this shape through it unconditionally would render
    the journal names but silently drop every date. When every element is a
    dict that yields a non-empty name-like value AND carries at least one of
    start_date/end_date, this instead joins "<name> (<start-end>)" per
    element (dates run through the same `format_date_range` every other cell
    in this file uses, keyed off the entry's own taxonomy code). Any other
    shape -- a plain string, a single dict, a list of str, a list where even
    one dict lacks a name or a date -- falls through to `_cell_text`
    unchanged, so a string input renders byte-identically and no unfamiliar
    shape is ever dropped for not looking like this one."""
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        pieces: list[str] = []
        for item in value:
            name = _cell_text(item)
            # #812 round 2: a per-item start_date/end_date can itself be
            # structured (same defect class this whole ticket fixes one
            # level up) -- coerce before format_date_range below.
            start_date = _cell_text(item.get('start_date') or '')
            end_date = _cell_text(item.get('end_date') or '')
            if not name or not (start_date or end_date):
                pieces = []
                break
            date_range = format_date_range(start_date, end_date, taxonomy_code)
            pieces.append(f"{name} ({date_range})" if date_range else name)
        if pieces:
            return "; ".join(pieces)
    return _cell_text(value)


def _service_boards_dates_text(fields: dict, taxonomy_code: str) -> str:
    """Coerce `start_date`/`end_date` before `format_date_range` for a
    Service on Boards (Q2) row.

    #812 round 2: `format_date_for_section` only `str()`s its input rather
    than raising, so a structured (list/dict) date printed its Python repr
    into the dates cell instead of crashing. Coerce before the `or ''` so a
    falsy coerced result (`None`/`[]` -> `''`) still collapses the same way
    the original code did (a pure move of `_fill_service_boards`'s round-2
    prelude, round 3, verify-D-812-R2 finding 2 -- no formula changed)."""
    start_date = _cell_text(fields.get('start_date') or '')
    end_date = _cell_text(fields.get('end_date') or '')
    return format_date_range(start_date, end_date, taxonomy_code) or ''


def _other_service_organization_text(fields: dict, taxonomy_code: str) -> str:
    """Compute the organization/agency cell text for an Other Service row.

    Q3 uses `agency`; others use `organization`/`committee_name`. Q4B/Q4C
    (editorial) use `journal_name` -- the one candidate that can carry a
    list of per-journal `{name, start_date, end_date}` records (#812,
    web204's multi-journal Q4C entry) -- only reached when the other three
    are all empty, so it routes through the date-aware journal coercer
    instead of the plain one this chain otherwise uses (a pure move of
    `_fill_other_service`'s round-2 prelude, round 3, verify-D-812-R2
    finding 2 -- no formula changed)."""
    organization = (fields.get('organization', '') or
                    fields.get('committee_name', '') or
                    fields.get('agency', ''))
    if organization:
        return _cell_text(organization)
    return _journal_name_cell_text(fields.get('journal_name', ''), taxonomy_code)


def _other_service_dates_text(fields: dict, taxonomy_code: str) -> str:
    """Coerce `start_date`/`end_date` before `format_date_range` for an
    Other Service row (#812 round 2; see `_service_boards_dates_text` above
    for the defect this guards -- a pure move, round 3, verify-D-812-R2
    finding 2, no formula changed)."""
    start_date = _cell_text(fields.get('start_date', ''))
    end_date = _cell_text(fields.get('end_date', ''))
    return format_date_range(start_date, end_date, taxonomy_code)


class ServiceSection:
    """Section Q writers, mixed into `WCMTemplateGenerator`."""

    def _fill_service(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill Q. EXTRAMURAL PROFESSIONAL RESPONSIBILITIES sections using tables.

        WCM template structure:
        - Leadership in Extramural Organizations: Table with Organization, Role, Dates
        - Service on Boards: Tables for Regional/National/International with Committee, Role, Org, Dates
        - Grant Reviewing/Study Sections: Table with Role, Organization, Dates
        - Editorial Activities: Multiple tables for Editor, Editorial Board, Journal Reviewing
        """
        # Collect all Q entries
        q_entries = []
        for code in SERVICE_TAXONOMY_CODES:
            q_entries.extend(entries_by_code.get(code, []))

        if not q_entries:
            return

        if self.verbose:
            print(f"Filling Service Activities ({len(q_entries)} entries)...")

        # Reroute Q2 entries that are actually journal reviewing to Q4D.
        # Handles misclassified single-line entries ("Reviewer" role for a
        # journal coded as Q2) and multi-line entries mixing both
        # activities; the routing decision itself is _route_q2_entries, a
        # free function that touches no docx object (#624 review).
        q2_entries = list(entries_by_code.get(BOARD_SERVICE_CODE, []))
        q4d_entries = list(entries_by_code.get(JOURNAL_REVIEWING_CODE, []))

        rerouted_to_journal, actual_board_entries = _route_q2_entries(q2_entries)

        if rerouted_to_journal and self.verbose:
            print(f"  Rerouted {len(rerouted_to_journal)} Q2 entries/lines to Journal Reviewing")

        q4d_entries.extend(rerouted_to_journal)
        q2_entries = actual_board_entries

        # Q2 entries go to "Service on Boards and/or Committees" - use National table by default
        if q2_entries:
            self._fill_service_boards(q2_entries)

        # Q4D entries go to "Journal Reviewing/Ad hoc Reviewing" table
        if q4d_entries:
            self._fill_journal_reviewing(q4d_entries)

        # Q4C and other Q4 entries (not Q4D) go to a general service table or bullet list
        other_q_entries = []
        for code in ['Q1', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C']:
            other_q_entries.extend(entries_by_code.get(code, []))

        if other_q_entries:
            self._fill_other_service(other_q_entries)

    def _fill_service_boards(self, entries: List[Dict]):
        """Fill Service on Boards and/or Committees tables.

        WCM template has tables for Regional/National/International.
        Uses cv_owner_location to classify geographic scope of each entry.
        Table structure: Name of Committee | Role | Organization | Dates
        """
        if not entries:
            return

        # Find the Service on Boards section
        service_idx = self._find_paragraph_with_text("Service on Boards")
        if service_idx is None:
            logger.warning(
                "Service on Boards: section not found in template; "
                "%d entries not rendered", len(entries))
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            scope_idx = None
            for i in range(service_idx, min(service_idx + 30, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope:
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(service_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            logger.warning(
                "Service on Boards: no Regional/National/International table "
                "found in template; %d entries not rendered", len(entries))
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
            print(f"  Service on Boards: {regional_count} Regional, {national_count} National, {intl_count} International")

        # Fill each table with its entries
        for scope, scope_entries in entries_by_scope.items():
            if not scope_entries:
                continue

            # Fall back to the National table when this scope has none of
            # its own -- but only if a National table was actually found;
            # if that's also missing, the entries have nowhere to go.
            table = tables_by_scope.get(scope) or tables_by_scope.get('National')
            if not table:
                logger.warning(
                    "Service on Boards: no %s or National table found; "
                    "%d %s entries not rendered",
                    scope, len(scope_entries), scope)
                continue

            sorted_entries = sort_entries_reverse_chronological(scope_entries)

            for entry in sorted_entries:
                fields = entry.get('extracted_fields', {}) or {}
                taxonomy_code = entry.get('taxonomy_code', 'Q2')

                committee = _cell_text(fields.get('committee_name'))
                role = _cell_text(fields.get('role')) or 'Member'
                organization = _cell_text(fields.get('organization'))

                # When Stage 4 merges committee name into the role field
                # (e.g., role="Chair, Ultrasound Committee"), split them apart
                if not committee and ', ' in role:
                    role_parts = role.split(', ', 1)
                    role_word = role_parts[0].strip().lower()
                    # Only split if the first part looks like a role title,
                    # including a qualified or chair-variant title Stage 4
                    # sometimes writes ("Past President", "Chairperson") --
                    # whole-phrase matching only (#624 review).
                    if _is_board_role_title(role_word):
                        role = role_parts[0].strip()
                        committee = role_parts[1].strip()

                # If still no committee, fall back to organization
                if not committee:
                    committee = organization
                    organization = ''
                dates = _service_boards_dates_text(fields, taxonomy_code)

                # If we don't have structured fields, parse from raw text
                if not committee:
                    text = entry.get('text', '')[:150]
                    committee = text

                # Add row to table
                row = table.add_row()
                num_cols = len(row.cells)

                # Populate based on number of columns
                # WCM template has 4 columns: Committee, Role, Organization, Dates
                if num_cols >= 4:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = organization or ''
                    row.cells[3].text = dates or ''
                elif num_cols >= 3:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = dates or ''
                else:
                    row.cells[0].text = f"{committee} ({role})" if role else (committee or '')
                    if num_cols > 1:
                        row.cells[1].text = dates or ''

                # Apply font formatting to each cell
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            _set_font(run)
                self.stats['entries_inserted'] += 1

    def _fill_extramural_leadership(self, entries: List[Dict]):
        """Fill Leadership in Extramural Organizations table (Q1 entries).

        Table structure: Organization | Role | Dates
        Handles multi-line entries that need splitting.
        """
        if not entries:
            return

        # Find Leadership in Extramural Organizations section
        section_idx = self._find_paragraph_with_text("Leadership in Extramural Organizations")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Leadership in Extramural")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1


        for entry in entries:
            original_text = entry.get('text', '')
            fields = entry.get('extracted_fields', {}) or {}

            # Check if extracted_fields has valid data - prefer using LLM extraction over raw parsing
            organization = fields.get('organization', '')
            role = fields.get('role', '')
            # #812 round 2: coerced here (not just inside _add_extramural_row
            # below) because format_date_range() runs BEFORE that call --
            # organization/role reach _add_extramural_row raw and are safe
            # (it coerces them itself), but these two feed format_date_range
            # directly a few lines down.
            start_date = _cell_text(fields.get('start_date', ''))
            end_date = _cell_text(fields.get('end_date', ''))

            # If we have at least organization or role from extraction, use that
            # The LLM extraction is more reliable than trying to parse garbled table text
            if organization or role:
                dates = format_date_range(start_date, end_date, 'Q1')
                if not organization:
                    organization = original_text[:100]
                self._add_extramural_row(table, organization, role, dates)
            else:
                # No useful extracted fields - try to parse from raw text
                lines = entry_lines(original_text)
                if len(lines) > 3:
                    # Multiple items merged - parse and split them
                    self._parse_extramural_leadership_lines(table, lines)
                else:
                    # Single entry without extracted fields - use raw text
                    self._add_extramural_row(table, original_text[:100], '', '')

    def _parse_extramural_leadership_lines(self, table, lines: list[str]) -> None:
        """Parse multiple extramural leadership lines and add rows.

        Handles complex patterns like:
        - Organization name followed by indented roles
        - Date ranges at end of lines or in separate date block
        - Two-column table extractions where roles and dates are in separate columns
        """

        # Patterns for date detection
        date_range_pattern = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?(?:\s*,\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)*)$', re.IGNORECASE)
        embedded_date_pattern = re.compile(r'\|\s*(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)\s*$', re.IGNORECASE)

        # First pass: categorize each line
        content_lines = []  # (text, embedded_date, is_org, is_role, original_idx)
        date_only_lines = []  # standalone dates

        for idx, line in enumerate(lines):
            line = line.strip()
            if not line or line.lower() in ['dates', 'organization', 'role', 'position']:
                continue

            # Check for date-only line (possibly multiple dates on one line)
            if date_range_pattern.match(line):
                # Split if multiple dates separated by newlines within the line
                date_parts = re.split(r'\s*\n\s*', line)
                for dp in date_parts:
                    dp = dp.strip()
                    if dp:
                        date_only_lines.append(dp)
                continue

            # Check for pipe-separated format: "Role | Date" or "Org | Role | Date"
            embedded_date = ''
            embedded_match = embedded_date_pattern.search(line)
            if embedded_match:
                embedded_date = embedded_match.group(1)
                line = line[:embedded_match.start()].strip().rstrip('|').strip()

            # Determine if this is an organization or a role. Uses the same
            # _matches_word_start matcher _is_known_org_line's veto uses, so
            # the two checks cannot disagree on a line (#658 round 2 --
            # this check used bare substring while the veto used `\b`-bounded
            # matching, and could each fire for different reasons on the
            # same line).
            line_lower = line.lower()
            is_org = _is_known_org_line(line_lower, EXTRAMURAL_ROLE_KEYWORDS)
            is_role = _matches_word_start(line_lower, EXTRAMURAL_ROLE_KEYWORDS) and not is_org

            # Indented lines are usually sub-items (roles under an org)
            is_indented = lines[idx].startswith('   ') or lines[idx].startswith('\t')
            if is_indented:
                is_role = True
                is_org = False

            content_lines.append((line, embedded_date, is_org, is_role, idx))

        # Second pass: build items with org-role pairing
        items = []
        current_org = None

        for line, embedded_date, is_org, is_role, _ in content_lines:
            if is_org:
                current_org = line
                # If org has embedded date, it's a standalone membership
                if embedded_date:
                    items.append((current_org, 'Member', embedded_date))
                else:
                    # Just setting context, will get roles below
                    pass
            elif is_role and current_org:
                # Role under current organization
                items.append((current_org, line, embedded_date))
            elif is_role:
                # Standalone role (no org context)
                items.append((line, '', embedded_date))
            else:
                # Generic content - could be org or description
                if len(line) > 50:  # Long text is probably a description
                    items.append((line, '', embedded_date))
                else:
                    current_org = line
                    if embedded_date:
                        items.append((current_org, '', embedded_date))

        # Third pass: match date-only lines to items without dates
        # Strategy: try to match dates in order they appear
        items_needing_dates = [(i, item) for i, item in enumerate(items) if not item[2]]

        if date_only_lines and items_needing_dates:
            # If roughly equal counts, match 1:1 in order
            if abs(len(date_only_lines) - len(items_needing_dates)) <= 2:
                for (item_idx, _), date in zip(items_needing_dates, date_only_lines):
                    org, role, _ = items[item_idx]
                    items[item_idx] = (org, role, date)
            else:
                # More dates than items or vice versa - match from top
                for i, (item_idx, _) in enumerate(items_needing_dates):
                    if i < len(date_only_lines):
                        org, role, _ = items[item_idx]
                        items[item_idx] = (org, role, date_only_lines[i])

        # Add rows for each item
        for org, role, date in items:
            # Skip items with no meaningful content
            if not org and not role:
                continue
            self._add_extramural_row(table, org, role, date)

    def _add_extramural_row(self, table, organization: object, role: object, dates: object):
        """Add a single row to extramural leadership table.

        The three shared callers (`_fill_extramural_leadership`,
        `_parse_extramural_leadership_lines`) pass fields.get(...) values
        straight through without coercing them first, so this is the one
        place all three converge before a `.text =` assignment -- coercing
        here once covers every caller (#812's class fix) instead of each of
        them separately."""
        organization = _cell_text(organization)
        role = _cell_text(role)
        dates = _cell_text(dates)
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = organization or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            combined = f"{organization} - {role}" if role else organization
            row.cells[0].text = combined or ''
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{organization} ({role}) {dates}".strip()

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _fill_journal_reviewing(self, entries: List[Dict]):
        """Fill Journal Reviewing/Ad hoc Reviewing table.

        Table structure: Journal / Organization Name | Dates
        """
        if not entries:
            return

        # Filter out header-like entries (e.g., just "Reviewer", "Journal", etc.)
        header_patterns = ['reviewer', 'journal', 'ad hoc', 'editorial', 'organization']
        filtered_entries = []
        for entry in entries:
            text = entry.get('text', '').strip().lower()
            # Skip if it's a single word that looks like a header
            if text and len(text.split()) <= 2 and any(text == h for h in header_patterns):
                continue
            filtered_entries.append(entry)

        if not filtered_entries:
            return

        # Find the Journal Reviewing section
        section_idx = self._find_paragraph_with_text("Journal Reviewing")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Ad hoc Reviewing")

        if section_idx is None:
            logger.warning(
                "Journal Reviewing: section not found in template; "
                "%d entries not rendered", len(filtered_entries))
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            logger.warning(
                "Journal Reviewing: table not found in template; "
                "%d entries not rendered", len(filtered_entries))
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(filtered_entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'Q4D')

            journal_name_value = fields.get('journal_name')
            if journal_name_value:
                journal = _journal_name_cell_text(journal_name_value, taxonomy_code)
            else:
                journal = _cell_text(fields.get('organization') or fields.get('committee_name'))
            # #812 round 2: coerce before format_date_range (see
            # _fill_service_boards above for why -- it only str()s a
            # structured value's Python repr into the cell).
            start_date = _cell_text(fields.get('start_date', '') or fields.get('year', ''))
            end_date = _cell_text(fields.get('end_date', ''))
            dates = format_date_range(start_date, end_date, taxonomy_code)

            if not journal:
                # Parse from raw text, but clean up common patterns
                raw_text = entry.get('text', '')[:100]
                # Remove "Reviewer" prefix if present
                journal = re.sub(r'^(?:Reviewer|Ad hoc Reviewer)[,:\s]*', '', raw_text, flags=re.IGNORECASE).strip()

            # Skip if still empty or too short
            if not journal or len(journal.strip()) < 3:
                continue

            row = table.add_row()
            row.cells[0].text = journal
            if len(row.cells) > 1:
                row.cells[1].text = dates

            # Apply font formatting to each cell
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        _set_font(run)
            self.stats['entries_inserted'] += 1

    def _fill_other_service(self, entries: List[Dict]):
        """Fill other Q entries that don't have specific tables.

        Uses bullet list format under appropriate section headers.
        """
        if not entries:
            return

        # Handle Q1 separately - it goes to Leadership in Extramural Organizations
        q1_entries = [e for e in entries if e.get('taxonomy_code') == LEADERSHIP_TAXONOMY_CODE]
        if q1_entries:
            self._fill_extramural_leadership(q1_entries)

        # Filter out Q1 from remaining entries
        entries = [e for e in entries if e.get('taxonomy_code') != LEADERSHIP_TAXONOMY_CODE]
        if not entries:
            return

        # Group by section. Q4A (Editor-in-Chief/Senior Editor/Co-Editor) is
        # editorial, not extramural leadership: it routes to its own
        # 'Editor/Co-Editor' table and must never resolve to
        # 'EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', which is Q1's anchor
        # (#454). Q4B (Associate/Guest Editor) and Q4C (Editorial Board
        # Member) go to Editorial Activities, not generic Professional
        # Service.
        sections = OTHER_SERVICE_SECTION_ROUTING

        entries_by_section = {}
        for entry in entries:
            code = entry.get('taxonomy_code', 'Q4')
            if code in sections:
                section_name = sections[code][0]
                if section_name not in entries_by_section:
                    entries_by_section[section_name] = {'entries': [], 'search_texts': sections[code][1]}
                entries_by_section[section_name]['entries'].append(entry)

        for section_name, data in entries_by_section.items():
            section_entries = data['entries']
            search_texts = data['search_texts']

            section_idx = None
            for search_text in search_texts:
                section_idx = self._find_paragraph_with_text(search_text)
                if section_idx is not None:
                    break

            if section_idx is None:
                continue

            # Try to find and use a table first
            table = self._find_table_after_paragraph(section_idx)
            if table and id(table._element) in self._cleared_tables:
                # Another filler already owns this table. Appending is wrong but
                # recoverable; clearing destroys content that has no appendix
                # fallback, because these Q codes are all in mapped_codes. This
                # is the backstop for #454 -- with Q4A routed correctly it should
                # never fire, so say so loudly if it does.
                logger.warning(
                    "section %r resolved to a table already filled by another "
                    "code; appending instead of clearing to avoid destroying it",
                    section_name)
                self.stats['tables_populated'] += 1
            elif table:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1
            if table:

                sorted_entries = sort_entries_reverse_chronological(section_entries)
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    taxonomy_code = entry.get('taxonomy_code', 'Q4')

                    role = _cell_text(fields.get('role', ''))
                    organization = _other_service_organization_text(fields, taxonomy_code)

                    # Q3 carries the study-section name in 'panel_name'.
                    # Append it to the agency/organization chain above --
                    # never promote it over agency, which is populated
                    # alongside panel_name on most Q3 entries and must not
                    # be evicted. Gated on Q3 because no other code is
                    # known to carry the field (#466).
                    panel_name = _cell_text(fields.get('panel_name', ''))
                    if taxonomy_code == GRANT_REVIEWING_CODE and panel_name:
                        if not organization:
                            organization = panel_name
                        elif _squash(panel_name) not in _squash(organization):
                            organization = f"{organization} - {panel_name}"

                    dates = _other_service_dates_text(fields, taxonomy_code)

                    # For Q4B/Q4C entries, try to parse role from raw text if missing
                    if not role and taxonomy_code in EDITORIAL_BOARD_CODES:
                        raw_text = entry.get('text', '')
                        # Pattern: "date | role | journal" or "role | journal"
                        # Match various editor/board roles
                        role_match = re.search(
                            r'\|\s*((?:Associate|Guest|Deputy|Senior|Managing|Executive|Review|Reviewing)\s*'
                            r'(?:Editor|editorial\s*board\s*member)|Editorial\s*(?:Board|board)\s*(?:Member|member)?)\s*\|',
                            raw_text, re.IGNORECASE
                        )
                        if role_match:
                            role = role_match.group(1).strip()
                        elif 'Editor' in raw_text or 'editor' in raw_text or 'Editorial' in raw_text:
                            # Try to extract role another way - check specific patterns
                            if 'Associate Editor' in raw_text:
                                role = 'Associate Editor'
                            elif 'Guest Editor' in raw_text:
                                role = 'Guest Editor'
                            elif 'Review editorial board member' in raw_text.lower():
                                role = 'Review Editorial Board Member'
                            elif 'Editorial board member' in raw_text.lower():
                                role = 'Editorial Board Member'
                            elif 'Editorial board' in raw_text.lower():
                                role = 'Editorial Board'

                    if not organization:
                        # Parse from raw text as fallback, but strip date prefix
                        raw_text = entry.get('text', '')
                        # Remove common date patterns from beginning
                        organization = re.sub(r'^\d{4}[-–]?\d{0,4}\s*\|?\s*', '', raw_text)[:100]
                        # If role already contains most of the organization text, don't duplicate
                        if role and organization and role.lower()[:30] in organization.lower():
                            organization = ''

                    row = table.add_row()
                    num_cols = len(row.cells)
                    if num_cols >= 3:
                        row.cells[0].text = role or ''
                        row.cells[1].text = organization or ''
                        row.cells[2].text = dates or ''
                    elif num_cols >= 2:
                        # Avoid duplicating content when role already contains full description
                        if role and organization and role.lower()[:20] in organization.lower():
                            row.cells[0].text = role
                        elif role and organization:
                            row.cells[0].text = f"{role}, {organization}"
                        else:
                            row.cells[0].text = role or organization
                        row.cells[1].text = dates
                    else:
                        row.cells[0].text = f"{organization} ({dates})" if dates else organization

                    # Apply font formatting to each cell
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                _set_font(run)
                    self.stats['entries_inserted'] += 1
