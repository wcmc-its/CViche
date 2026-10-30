"""Text-normalisation helpers shared by the run doctor, the segmentation
regression harness and the render checks.

`norm`, `fold_quotes` and `squash` are three different keys, not three
spellings of one: compare a value only with another value passed through the
same helper. `norm` folds Latin, Greek and Cyrillic accents, collapses
whitespace and lowercases; `fold_quotes` maps typographic quotes to ASCII and
changes nothing else; `squash` drops all whitespace and lowercases, and folds
neither accents nor quotes. Each takes a string or None; None reads as "" (a
missing JSON field), and any other value is read as `str(value)`, so a 0 or a
False an upstream stage wrote stays visible instead of vanishing.
"""

import re
import unicodedata
from collections.abc import Callable, Collection, Iterable
from typing import NamedTuple

# Substantive-line threshold: shorter lines ("2016", "PhD", bare bullets)
# match by accident and only add noise to the coverage metric.
SUBSTANTIVE_LINE_CHARS = 15


#: The scripts whose accents `_fold_marks` drops, as the prefix of a base
#: letter's Unicode name. A CV writes these names both with and without their
#: accents ('Müller', 'Muller'). A mark on a letter of any other script is part
#: of the letter (Japanese dakuten, a Devanagari virama, Hebrew points) and is
#: kept.
_ACCENT_FOLDED_SCRIPTS = ("LATIN ", "GREEK ", "CYRILLIC ")


def _drops_marks_after(char: str) -> bool:
    """Whether the combining marks that follow `char` are dropped: those on a
    Latin, Greek or Cyrillic letter, and those on a character that is no
    letter or mark at all. NFKD writes a spacing accent ('Rohe\u00b4') as a
    space and a combining mark, and that mark is dropped, as before."""
    if unicodedata.category(char)[0] not in "LM":
        return True
    return unicodedata.name(char, "").startswith(_ACCENT_FOLDED_SCRIPTS)


def _fold_marks(text: str) -> str:
    """Drop the combining marks of Latin, Greek and Cyrillic letters ('Müller'
    -> 'Muller', Greek tonos, Cyrillic breve) so an accented word tokenizes the
    same as its base letters (#541). A mark on a letter of another script is
    kept: 'が' and 'か' are different words. NFKD also folds compatibility
    forms on purpose (the ligature 'ﬁ' -> 'fi', '™' -> 'TM', a full-width 'Ａ'
    -> 'A'). NFC at the end recomposes what is kept, Hangul jamo and the kept
    marks, so a CJK string keeps its character count (CJK is excluded from
    render tokens, #722). Pure-ASCII input is returned untouched,
    byte-identical."""
    if text.isascii():
        return text
    kept: list[str] = []
    base_folds = False
    for char in unicodedata.normalize("NFKD", text):
        if not unicodedata.combining(char):
            base_folds = _drops_marks_after(char)
        elif base_folds:
            continue
        kept.append(char)
    return unicodedata.normalize("NFC", "".join(kept))


def _as_text(text: str | None) -> str:
    """`text` as a string: None (a missing field) is "", any other value its
    `str()`. Not `str(text or "")`, which also turns 0 and False into ""."""
    return "" if text is None else str(text)


def norm(text: str | None) -> str:
    # Lowercase AFTER folding: NFKD can yield uppercase ("™" -> "TM").
    return _fold_marks(" ".join(_as_text(text).split())).lower()


#: Typographic apostrophes and quotes mapped to their ASCII forms. A source
#: page-break header can carry U+2019 where the stage 1a hierarchy node for
#: the same header carries U+0027 ("(CONT'D)", #1232), so a comparison that
#: does not fold them reports a header 1a found as missing.
_QUOTE_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
})


def fold_quotes(text: str | None) -> str:
    """`text` with typographic apostrophes and double quotes replaced by
    their ASCII forms, so two spellings of one phrase compare equal. Not part
    of `norm`: coverage and render matching already tokenize across
    apostrophes, and widening `norm` would move every metric built on it."""
    return _as_text(text).translate(_QUOTE_FOLD)


def squash(text: str | None) -> str:
    """Whitespace-FREE normalization for the coverage check: stage 2 joins
    text across in-paragraph line breaks with no whitespace at all
    ('Present position:' + break + 'Attending' -> 'position:Attending'),
    and tab-joined label/value lines re-emerge with tabs dropped. Comparing
    with whitespace removed on both sides is immune to all of that."""
    return re.sub(r"\s+", "", _as_text(text)).lower()


#: A pipe or tab row is record-shaped only when it is longer than this: a
#: shorter one is a label and its value ("Phone\t555-0100"), not a record.
RECORD_ROW_MIN_CHARS = 60


def looks_like_record(line: str) -> bool:
    """Whether `line` is a candidate record row: longer than
    `RECORD_ROW_MIN_CHARS` and holding a ' | ' or a tab. A prefilter, not a
    classifier. False says only that the line is not a delimited row. The
    doctor's record counts OR it with their own date-line test, the mega-entry
    count (`segmentation_regression`) counts delimited rows only, and
    `tab_record_segments` splits a tab line whatever this returns."""
    line = line.strip()
    return len(line) > RECORD_ROW_MIN_CHARS and (" | " in line or "\t" in line)


# One date range of a record line: "2009-2011", "1996-8", "5/2017-10/2020",
# "2012-Jun 2015", "2013-present", "07/01/2012-05/31/2014", "07/2019-02/21".
# A month, or a month and day, before either year ("5/2017", "07/01/2012") is
# part of the range, so the match is the whole range and not the "2017-10"
# inside it; after a month the end year may have two digits. The start year
# stops at 2039 so a page range ("2064-71") is not read as one. The match is
# the range's identity for `tab_record_segments`' count of distinct ranges;
# nothing checks that the end follows the start.
_DATE_RANGE_RE = re.compile(
    r"\b(?:\d{1,2}/){0,2}(?:19\d{2}|20[0-3]\d)\s*[-\u2013\u2014]\s*"
    r"(?:(?:(?:\d{1,2}/){1,2}(?:(?:19|20)\d{2}|\d{2})"
    r"|(?:[A-Za-z]{3,9}\.? )?(?:(?:19|20)\d{2}|\d{1,2}))\b"
    r"|(?i:present|current|ongoing|now|to date)\b)")

# A tab part must be at least this long to be a dated record and at most the
# second length: a longer part is prose about a record, not one. Its date range
# sits within `DATED_SEGMENT_EDGE_CHARS` of either end: a range in the middle of
# the text belongs to a sentence or to a record wrapped across two parts.
# ponytail: 12, not the 20 of `RECORD_DATE_LINE_MIN_CHARS` for a date-led line,
# keeps an officer post beside its society's line ("President 2011-2013", 19
# chars; "Member 1975-1978", 16) a record of its own (#1243). A shorter part
# ("Rep 2001-02") stays a detail, and so does a part shorter than
# `DATED_SEGMENT_OFFICE_FREE_MIN_CHARS` that names no office (`_OFFICE_WORD_RE`).
# Without the office, on the #1243 census (460 stored runs, 2026-10-29) the 12
# floor wrote 4 bullets that were no record: the two per-state terms of a
# delegate row that renders with its whole span ("<State> 2005-2008", BNYLDF
# 209), and, once an 18-character one-year list item ("<Company> (2015)")
# pushed its line over the half-dated share, two fragments of a comma list
# wrapped across tab parts (EHZFOH 247). A heading or a detail of that length
# beside dated records ("Committees 2010-15", "Term ended 2015-16") was written
# the same way. With it, 12 changes the parts of 42 entries and the render of 5,
# each a post the render had lost. A short part still needs words beside its
# range, and its line two distinct ranges and half its parts dated. Revisit if
# an office the list lacks turns up lost in a short part, or a short part that
# is no record (a room or course number with a year) splits a line.
DATED_SEGMENT_MIN_CHARS = 12
DATED_SEGMENT_OFFICE_FREE_MIN_CHARS = 20
DATED_SEGMENT_MAX_CHARS = 200
DATED_SEGMENT_EDGE_CHARS = 12
# An office or a seat a short dated part names: what makes "Co-chair 2012-2014"
# a post of its own where "<State> 2005-2008" is a term of the role beside it.
# A body ("Committees", "Board") is not one: a short part naming only a body is
# a heading over the posts.
_OFFICE_WORD_RE = re.compile(
    r"\b(?:member|fellow|chair\w*|president|secretary|treasurer|director|officer"
    r"|delegate|trustee|councill?or|governor|editor|founder|coordinator|liaison|representative"
    r"|dean)\b", re.IGNORECASE)
# A line is a run of records only when its dated parts give at least this many
# different date ranges and make up at least this share of all its parts.
DATED_SEGMENT_MIN_DISTINCT_RANGES = 2
DATED_SEGMENT_MIN_SHARE = 0.5
# A part states a record only with words besides its date range: three letters
# anywhere in the part once the range is cut out. A bare date column fails.
_WORDS_BESIDE_RANGE_RE = re.compile(r"[^\W\d_]{3,}")
# A year standing alone, outside any date range: the date of a record held in
# one year ("Founding Member, 1979", "2004  Quimby Farrow"). Same year span
# as `_DATE_RANGE_RE`'s start year.
_LONE_YEAR_RE = re.compile(r"\b(?:19\d{2}|20[0-3]\d)\b")
# A part that ends in a colon labels the parts after it ("Awards/Distinctions:",
# "Papers related to dissertation work:"): they are details of one record,
# whatever years they carry.
_DETAIL_LABEL_SUFFIX = ":"


def _join_wrapped_parts(parts: list[str]) -> list[str]:
    """`parts` with a part that stage 2 broke inside a parenthesis joined back:
    "... (Supported by" and "Acme Labs, 1998 - 2001)" are one line. A part
    joins only the text before it that leaves a parenthesis open. A stray ")"
    in a part does not join it to a closed one: "b) Vice Chair ..." is the
    next item of a list, not the end of the item before it."""
    joined: list[str] = []
    for part in parts:
        if joined and joined[-1].count("(") > joined[-1].count(")"):
            joined[-1] = f"{joined[-1]} {part}"
        else:
            joined.append(part)
    return joined


def edge_date_range(part: str) -> str | None:
    """The first date range of `part`, in reading order, that starts or ends
    it, or None. When both edges hold one, the range at the start wins: it is
    the earlier match."""
    for match in _DATE_RANGE_RE.finditer(part):
        if match.start() <= DATED_SEGMENT_EDGE_CHARS or len(part) - match.end() <= DATED_SEGMENT_EDGE_CHARS:
            return match.group()
    return None


def _edge_lone_year(part: str) -> str | None:
    """The first year of `part` that stands outside every date range and starts
    or ends it, or None ("Research Affairs Committee, 1985")."""
    outside_ranges = _DATE_RANGE_RE.sub(lambda match: " " * len(match.group()), part)
    for match in _LONE_YEAR_RE.finditer(outside_ranges):
        if match.start() <= DATED_SEGMENT_EDGE_CHARS or len(part) - match.end() <= DATED_SEGMENT_EDGE_CHARS:
            return match.group()
    return None


def _is_dated_record_part(part: str) -> bool:
    """Whether `part` is long enough to be a record, not prose, and has words
    besides its dates (`_WORDS_BESIDE_RANGE_RE`), an office among them when it
    is short (`DATED_SEGMENT_OFFICE_FREE_MIN_CHARS`)."""
    undated = _LONE_YEAR_RE.sub(" ", _DATE_RANGE_RE.sub(" ", part))
    return (DATED_SEGMENT_MIN_CHARS <= len(part) <= DATED_SEGMENT_MAX_CHARS
            and _WORDS_BESIDE_RANGE_RE.search(undated) is not None
            and (len(part) >= DATED_SEGMENT_OFFICE_FREE_MIN_CHARS
                 or _OFFICE_WORD_RE.search(undated) is not None))


def tab_record_segments(line: str) -> list[str]:
    """The tab-joined parts of `line` that each state a dated record, or [] when
    the line is not a run of them.

    Stage 2 fuses the paragraphs of one entry with tabs, so several records can
    sit on one line that `looks_like_record` counts once (#1243: committee and
    office lines of one society, trainees of one lab). A part is a record when
    it holds a date range and words beside it; a bare date column ("2010-2013")
    is the date of a neighbouring part. The line is a run of records only when
    the dated parts give at least two different ranges and are at least half of
    its parts: an entry with one dated record and dated details (an award list,
    a role and its period, a description that repeats the title's years) is one
    record. A part's range is the one at its start or end (`edge_date_range`).
    Each condition removed a case where the recovery pass wrote a detail of one
    record a second time beside its row, in the render of 302 corpus CVs.

    A part dated by one year at its start or end ("Fellow of the Society,
    1985") is a record too, once the line has its two ranges: a society's
    one-year posts and a lab's one-year trainees sit beside its ranged ones.
    Not on a line with a part that ends in a colon: the parts after such a
    label are details of one record (a mentee's awards and papers).
    """
    parts = _join_wrapped_parts([part.strip() for part in line.split("\t") if part.strip()])
    ranged = [(part, edge_date_range(part)) for part in parts]
    distinct = {date_range for part, date_range in ranged
                if date_range and _is_dated_record_part(part)}
    if len(distinct) < DATED_SEGMENT_MIN_DISTINCT_RANGES:
        return []
    years_date_parts = not any(part.endswith(_DETAIL_LABEL_SUFFIX) for part in parts)
    dated = [part for part, date_range in ranged
             if (date_range or (years_date_parts and _edge_lone_year(part)))
             and _is_dated_record_part(part)]
    return dated if len(dated) / len(parts) >= DATED_SEGMENT_MIN_SHARE else []


# --- Dated clauses -----------------------------------------------------------
#
# A line can hold several records with no tab between them, joined by "; ",
# ", ", ". " or just the next date ("Member, <society>, 1995-96. Treasurer,
# <society>, 1997-99"). The run doctor's `multi_record_coverage` cuts such a
# line at its dates to find the records stage 4 left out; stage 6's recovery
# pass cuts it the same way to re-emit them (`dated_record_clauses`, #1243).
# Both read the helpers below, so a date, a payload and an aside mean one thing.

#: A month or term word, as dates write it.
MONTH_PATTERN = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?"
                 r"|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?"
                 r"|dec(?:ember)?|spring|summer|fall|autumn|winter)\.?")
#: A word that belongs to a date, not to what the record is.
DATE_WORD_RE = re.compile(rf"^(?:{MONTH_PATTERN}|present|current|ongoing|date|now)$",
                          re.IGNORECASE)
#: What may sit between two years of ONE date: "1990-1992", "March 2001 -
#: June 2004", "1987, 1988", "2003 and 2005".
DATE_GAP_RE = re.compile(
    rf"^(?:[\s,\-–—/.:()&]|\d|(?:to|through|thru|and|present|current|ongoing"
    rf"|{MONTH_PATTERN})\b)*$", re.IGNORECASE)
#: A word for `has_payload`: three or more letters.
_PAYLOAD_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_PAYLOAD_FILLER = frozenset({"and", "the"})
#: An aside that opens a clause: "(Chair). ...", " - (see ...".
_PAREN_OPENING_RE = re.compile(r"^[\s\-–—,.:;]*\(")
_PAREN_LEAD_CHARS = frozenset(",.;:-–—")
#: An undated part names a record when it carries a role or rank and an
#: institution: "Assistant Professor, <university> College of Medicine" beside
#: the hospital post the record kept (TAUBPU-01). A PI named in a project's
#: credit line is not the owner's role, so "investigator" is not a title.
TITLE_WORD_RE = re.compile(
    r"\b(?:professor|instructor|lecturer|fellow|director|chief|chair(?:man|person)?"
    r"|attending|surgeon|physician|resident|member|president|dean|head|scientist"
    r"|consultant|officer|editor|coordinator|manager|advisor|associate|assistant)\b",
    re.IGNORECASE)
#: A clause with at least this many lowercase words, and more lowercase words
#: than capitalised ones, is a sentence about the record ("in recognition of
#: ...", "who then went on to ..."), not a record. Titles, roles and
#: institutions are capitalised; a short role phrase in sentence case
#: ("Clinical skills tutor") stays below the floor.
PROSE_MIN_LOWERCASE_WORDS = 4
#: A word `is_prose` counts: four or more letters.
PROSE_WORD_RE = re.compile(r"[^\W\d_]{4,}")


class Clause(NamedTuple):
    """One record-shaped stretch of an entry's text and the years it writes."""
    text: str
    years: frozenset[str]


class DateAnchor(NamedTuple):
    """One written date: its span in the text and the years it holds."""
    start: int
    end: int
    years: frozenset[str]


def has_payload(text: str) -> bool:
    """Whether a stretch of entry text names anything besides a date."""
    return any(not DATE_WORD_RE.match(word) and word.lower() not in _PAYLOAD_FILLER
               for word in _PAYLOAD_WORD_RE.findall(text))


def is_prose(text: str) -> bool:
    """A sentence about a record rather than a record; see
    PROSE_MIN_LOWERCASE_WORDS."""
    words = [word for word in PROSE_WORD_RE.findall(text) if not DATE_WORD_RE.match(word)]
    lowercase = sum(1 for word in words if word[0].islower())
    return (lowercase >= PROSE_MIN_LOWERCASE_WORDS
            and lowercase > len(words) - lowercase)


def join_date_anchors(text: str, dates: Iterable[DateAnchor],
                      counts: Callable[[DateAnchor, bool], bool] | None = None,
                      ) -> list[DateAnchor]:
    """`dates`, the dates `text` writes in reading order, as its anchors: a
    date joins the anchor before it when only a date gap (`DATE_GAP_RE`)
    separates them, so "1990-1992" or "March 2001 - June 2004" is one. A
    `counts` that returns False for a date (and whether it would join the
    anchor before it) leaves that date out."""
    anchors: list[DateAnchor] = []
    for date in dates:
        joined = bool(anchors) and DATE_GAP_RE.match(text[anchors[-1].end:date.start]) is not None
        if counts is not None and not counts(date, joined):
            continue
        if joined:
            last = anchors[-1]
            anchors[-1] = DateAnchor(last.start, date.end, last.years | date.years)
        else:
            anchors.append(date)
    return anchors


def _is_parenthetical(text: str) -> bool:
    """An aside of the clause before it: the clause opens a parenthesis that
    never closes, or that closes with nothing after it. A parenthesis inside
    it ("(<board> (<acronym>) approved, <year>") closes before it does."""
    opening = _PAREN_OPENING_RE.match(text)
    if not opening:
        return False
    depth = 1
    for position in range(opening.end(), len(text)):
        if text[position] == "(":
            depth += 1
        elif text[position] == ")":
            depth -= 1
            if not depth:
                return not has_payload(text[position + 1:])
    return True


def _opens_inside_parens(part: str, position: int) -> bool:
    """Whether the words after `position` sit inside a parenthesis an earlier
    clause opened -- "(won an award <year>, <meeting>, <city>)" is that
    clause's aside, not a record. Punctuation, digits and closing parentheses after
    `position` are stepped over first."""
    depth = part.count("(", 0, position) - part.count(")", 0, position)
    for char in part[position:]:
        if char == ")":
            depth -= 1
        elif not (char.isspace() or char.isdigit() or char in _PAREN_LEAD_CHARS):
            break
    return depth > 0


class ClauseCut(NamedTuple):
    """A clause `_clause_cuts` cuts, and whether it is an aside of the clause
    before it (`_is_parenthetical`, `_opens_inside_parens`)."""
    clause: Clause
    aside: bool


def _clause_cuts(part: str, anchors: list[DateAnchor]) -> list[ClauseCut]:
    """One part cut at its date `anchors`. A part whose dates are not
    separated by any words is one clause (a record with a list of dates);
    otherwise each date takes the words after it when the part opens with a
    date ("<year> <talk> <year> <talk>"), else the words before it ("<role>,
    <years> <role>, <years>")."""
    if not anchors:
        return []
    between = [part[a.end:b.start] for a, b in zip(anchors, anchors[1:], strict=False)]
    if not any(has_payload(gap) for gap in between):
        return [ClauseCut(Clause(part, frozenset().union(*(a.years for a in anchors))), False)]
    leading = not has_payload(part[:anchors[0].start])
    cuts = []
    for i, anchor in enumerate(anchors):
        if leading:
            end = anchors[i + 1].start if i + 1 < len(anchors) else len(part)
            text = part[anchor.start:end]
        else:
            text = part[anchors[i - 1].end if i else 0:anchor.end]
        aside = bool(i) and (_is_parenthetical(text)
                             or (leading and _opens_inside_parens(part, anchor.end)))
        cuts.append(ClauseCut(Clause(text, anchor.years), aside))
    return cuts


def date_clauses(part: str, anchors: list[DateAnchor]) -> list[Clause]:
    """One part's dated clauses, cut at its date `anchors` (`_clause_cuts`).
    An aside of the clause before is no clause."""
    return [cut.clause for cut in _clause_cuts(part, anchors) if not cut.aside]


#: A date a record clause ends or opens on: a date range or a year standing
#: alone (`_DATE_RANGE_RE`, `_LONE_YEAR_RE`).
_RECORD_DATE_RE = re.compile(rf"{_DATE_RANGE_RE.pattern}|{_LONE_YEAR_RE.pattern}")
# A lone year is part of a name, not the date of a record, when it follows an
# event word ("<society> Annual Meeting 2020", "Class of 2010"), starts an
# event name away from a clause boundary ("Committee, 2009 Western Regional
# Meeting"), or sits between an all-caps acronym and a capitalised word ("AACR
# 2011 Scientific Program Committee"). It is a date after a month or term
# ("May 2012", "Fall 2010"), after "MM/" ("5/2012"), after a two-letter state
# code ("<city>, MD 1999") and when it joins the date before it ("1980 to
# 1982").
_EVENT_WORDS = r"meeting|conference|symposium|congress|convention|assembly|summit|workshop|course"
_EVENT_BEFORE_YEAR_RE = re.compile(rf"\b(?:{_EVENT_WORDS}|class of)$", re.IGNORECASE)
_EVENT_AFTER_YEAR_RE = re.compile(
    rf"^(?:[A-Z0-9][\w&/.'’-]*\s+){{0,6}}?(?:annual|{_EVENT_WORDS})\b", re.IGNORECASE)
_MONTH_BEFORE_YEAR_RE = re.compile(rf"(?:\b{MONTH_PATTERN},?|\b\d{{1,2}}/)$", re.IGNORECASE)
_STATE_BEFORE_YEAR_RE = re.compile(r"\b[A-Z]{2}\.?,?$")
_ACRONYM_BEFORE_YEAR_RE = re.compile(r"\b[A-Z]{3,}$")
_CLAUSE_BOUNDARY_CHARS = frozenset(".;|:(•*")
# A word: what a year's neighbour is read as, and what a name is compared by.
_NAME_WORD_RE = re.compile(r"[^\W_]+")
# A line that opens with a dated parenthesis ("(Vice Chair <years>) (Chair
# <years>)") holds the offices of the record on the line before it, not
# records of its own. A list number ("(14) <committee> ...") is no aside.
_ASIDE_LINE_RE = re.compile(rf"^\s*\([^()]*{_LONE_YEAR_RE.pattern}")
# A clause the recovery may re-emit is at least the first length and at most
# the second: a shorter one is a fragment of a record ("Chair 2010"), a longer
# one a paragraph about one.
# ponytail: these rules were tuned on the #1243 census farm (460 stored runs,
# 2026-10-30), where the recovery writes 39 clauses in 23 runs: 38 records the
# render had lost, and 1 membership that another entry writes in other words,
# which no word test tells apart. On 117 held-out runs it writes 4, all lost
# records. Revisit if a recovered clause turns up printed twice, or a run of
# records the census did not see (a list with no title or body word) is lost.
DATED_CLAUSE_MIN_CHARS = 15
DATED_CLAUSE_MAX_CHARS = 250
# A unit is a run of records only when at least this many of its clauses are
# records (`_is_record_clause`) and they make up at least this share of all.
DATED_CLAUSE_MIN_RECORDS = 2
DATED_CLAUSE_MIN_SHARE = 0.5
# The punctuation a clause loses at either end: what joined it to the next.
_CLAUSE_EDGE_CHARS = " \t,;.:()-–—•*"
# A clause opens with a capital or a digit, past any punctuation: one that
# opens in lower case continues the sentence before it.
_CLAUSE_OPENING_RE = re.compile(r"^[\W\d_]*[A-Z0-9]")
# An aside of the clause before it (`ClauseCut.aside`) is a record only when
# it is an office held within that record: an office word (`_OFFICE_WORD_RE`)
# and at most this many words besides its date ("(Chairman, <years>)" after
# an advisory board's own span). A longer aside is a note on the record (a
# case it heard, an approval it received).
OFFICE_ASIDE_MAX_WORDS = 3
# A pointer to a record written elsewhere ("<years> see previous section"):
# the record it names is not on this line.
_CROSS_REFERENCE_RE = re.compile(
    r"\b(?:see|cf\.?)\s+(?:also\s+)?(?:the\s+)?(?:previous|prior|above|below|next|following"
    r"|earlier|later|other|section|sections|page|pages|list|table|appendix|attached)\b"
    r"|\b(?:as|listed|noted|described)\s+(?:above|below|elsewhere)\b", re.IGNORECASE)
# A body a record belongs to. With `TITLE_WORD_RE`'s titles, what makes a
# clause name a record rather than a detail of one. Wider than the doctor's
# `_INSTITUTION_WORD_RE`, which judges undated parts: a dated clause may name
# a meeting, a journal or a working group.
_BODY_WORD_RE = re.compile(
    r"\b(?:society|association|academy|college|federation|council|committee|board"
    r"|foundation|forum|network|consortium|alliance|league|union|club|task force"
    r"|working group|work group|workgroup|panel|commission|subcommittee|section"
    r"|chapter|caucus|institute|organization|cent(?:er|re)|program|journal"
    r"|initiative|conference|meeting|symposium|congress|course"
    r"|school|university|hospital|department|division)\b", re.IGNORECASE)


def _named_year(unit: str, date: DateAnchor, names: Collection[str]) -> bool:
    """Whether one of `names` writes the lone year `date` of `unit` with the
    word beside it ("<title> Act 2010"): the record's own title or name holds
    the year, so it is part of that name. Compared word by word, case folded."""
    year = unit[date.start:date.end]
    before = _NAME_WORD_RE.findall(unit[:date.start])[-1:]
    after = _NAME_WORD_RE.findall(unit[date.end:])[:1]
    phrases = [f" {before[0].lower()} {year} "] if before else []
    phrases += [f" {year} {after[0].lower()} "] if after else []
    spaced = [f" {' '.join(_NAME_WORD_RE.findall(name.lower()))} " for name in names]
    return any(phrase in name for phrase in phrases for name in spaced)


def _year_in_name(unit: str, date: DateAnchor, names: Collection[str] = ()) -> bool:
    """Whether `date`, a date of `unit`, is a year inside a name rather than
    a record's date (see `_EVENT_WORDS`), or one of the record's `names`
    writes it (`_named_year`). A range never is."""
    if not _LONE_YEAR_RE.fullmatch(unit[date.start:date.end]):
        return False
    before, after = unit[:date.start].rstrip(), unit[date.end:].lstrip()
    if not before or _MONTH_BEFORE_YEAR_RE.search(before) or _STATE_BEFORE_YEAR_RE.search(before):
        return False
    return bool(_EVENT_BEFORE_YEAR_RE.search(before)
                or (before[-1] not in _CLAUSE_BOUNDARY_CHARS and _EVENT_AFTER_YEAR_RE.match(after))
                or (_ACRONYM_BEFORE_YEAR_RE.search(before) and after[:1].isupper())
                or _named_year(unit, date, names))


def record_date_anchors(unit: str, names: Collection[str] = ()) -> list[DateAnchor]:
    """The record dates of `unit`, its ranges and lone years joined into one
    anchor where only a date gap separates them (`join_date_anchors`), less
    the years inside a name (`_year_in_name`; `names` are the record's own
    titles and names). A year that joins the date before it always counts."""
    dates = (DateAnchor(match.start(), match.end(), frozenset(_LONE_YEAR_RE.findall(match.group())))
             for match in _RECORD_DATE_RE.finditer(unit))
    return join_date_anchors(unit, dates,
                             lambda date, joined: joined or not _year_in_name(unit, date, names))


def _is_record_clause(clause: str) -> bool:
    """Whether `clause` reads as a record: `DATED_CLAUSE_MIN_CHARS` to
    `DATED_CLAUSE_MAX_CHARS` long, naming something besides its date
    (`has_payload`), not a sentence (`is_prose`) nor a pointer to a record
    written elsewhere (`_CROSS_REFERENCE_RE`), opening with a capital or a
    digit, and naming a title (`TITLE_WORD_RE`) or a body (`_BODY_WORD_RE`)."""
    return (DATED_CLAUSE_MIN_CHARS <= len(clause) <= DATED_CLAUSE_MAX_CHARS
            and has_payload(clause) and not is_prose(clause)
            and _CROSS_REFERENCE_RE.search(clause) is None
            and _CLAUSE_OPENING_RE.match(clause) is not None
            and (TITLE_WORD_RE.search(clause) or _BODY_WORD_RE.search(clause)) is not None)


def noted_clause_head(clause: str) -> str | None:
    """The text of a dated clause before its parenthetical note, or None
    when it has none: a parenthesis that holds a word besides its dates and
    names no office ("<committee> (elected; <years>)"). The note says how
    the record was held, not which record it is, so the head names the
    record. An office in the parenthesis ("(founder; Chair <years>)") is a
    record of its own within it, and keeps the whole clause."""
    head, opened, note = clause.partition("(")
    if not opened or not _undated_words(note) or _OFFICE_WORD_RE.search(note):
        return None
    return head.strip(_CLAUSE_EDGE_CHARS)


def _written_dates(unit: str, anchor: DateAnchor) -> frozenset[str]:
    """The dates `anchor` joins, as `unit` writes them: two anchors with the
    same are one record's date written twice ("<year>: <talk>, <day> <month>
    <year>")."""
    return frozenset(match.group() for match in _RECORD_DATE_RE.finditer(unit, anchor.start, anchor.end))


def _undated_words(text: str) -> list[str]:
    """The words of `text` (`_PAYLOAD_WORD_RE`) that are not date words."""
    return [word for word in _PAYLOAD_WORD_RE.findall(text) if not DATE_WORD_RE.match(word)]


def _is_office_aside(clause: str) -> bool:
    """Whether the aside `clause` is an office held within the record before
    it (`OFFICE_ASIDE_MAX_WORDS`)."""
    return (len(_undated_words(clause)) <= OFFICE_ASIDE_MAX_WORDS
            and _OFFICE_WORD_RE.search(clause) is not None)


def dated_record_clauses(unit: str, names: Collection[str] = ()) -> list[str]:
    """The records of `unit`, one line or tab part, when it holds several of
    them cut by their dates, or [] when it reads as one record.

    Stage 2 can leave several records on one line with no tab between them
    ("Member, <society>, 1995-96. Treasurer, <society>, 1997-99"; "2006
    <talk>. 2008 <talk>."), and `tab_record_segments` splits only on tabs
    (#1243). The unit is cut at its record dates (`record_date_anchors`)
    into clauses (`date_clauses`), date-led or date-trailing; it needs two
    different dates with words between them. Each clause loses the
    punctuation at its ends. It is a run of records only when
    `DATED_CLAUSE_MIN_RECORDS` clauses or more are records
    (`_is_record_clause`) and they are `DATED_CLAUSE_MIN_SHARE` of its
    clauses or more; those are returned, in order. An aside of the clause
    before (`ClauseCut.aside`) counts only when it is an office held within
    it (`_is_office_aside`). `names`, the record's own titles and names,
    tell a year inside a title from a record's date (`_named_year`).

    Not a run of records: narrative (`is_prose`), and a line that opens with
    a dated parenthesis, the offices of the record before it
    (`_ASIDE_LINE_RE`).
    """
    if is_prose(unit) or _ASIDE_LINE_RE.match(unit):
        return []
    anchors = record_date_anchors(unit, names)
    if len({_written_dates(unit, anchor) for anchor in anchors}) < 2:
        return []
    stripped = [(cut.clause.text.strip(_CLAUSE_EDGE_CHARS), cut.aside) for cut in _clause_cuts(unit, anchors)]
    clauses = [text for text, aside in stripped if not aside or _is_office_aside(text)]
    records = [clause for clause in clauses if _is_record_clause(clause)]
    if len(records) < DATED_CLAUSE_MIN_RECORDS or len(records) < DATED_CLAUSE_MIN_SHARE * len(clauses):
        return []
    return records


#: A whole value in square brackets: "[Title not provided]".
_BRACKETED_RE = re.compile(r"\[[^\[\]]*\]")


def is_placeholder_title(title: object, source_text: str) -> bool:
    """True when `title` is a bracketed placeholder an LLM wrote in place of a
    title the source lacks ("[Title not provided]", "[Title not available]",
    #446: EBYSBC HFAJCC-05, XWNZWW-02). A bracketed value the source itself
    carries is the CV's own text, not a placeholder (`_source_carries`)."""
    if not isinstance(title, str):
        return False
    value = title.strip().rstrip(".").strip()
    return bool(_BRACKETED_RE.fullmatch(value)) and not _source_carries(value, source_text)


def _source_carries(bracketed: str, source_text: str) -> bool:
    """Whether the source holds the words of `bracketed` as a whole phrase,
    with or without the brackets: "[AUC]" is vouched for by "[AUC]" or "AUC",
    not by the "auc" of "auction". Both sides are compared through `norm`."""
    inner = norm(bracketed[1:-1])
    phrase = rf"(?<!\w){re.escape(inner)}(?!\w)"
    return bool(inner) and re.search(phrase, norm(source_text)) is not None
