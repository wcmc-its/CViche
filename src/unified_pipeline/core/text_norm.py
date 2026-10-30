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
