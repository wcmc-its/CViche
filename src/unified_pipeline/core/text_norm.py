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

# A tab part must be at least this long to be a dated record (the same floor as
# `RECORD_DATE_LINE_MIN_CHARS` for a date-led line) and at most the second
# length: a longer part is prose about a record, not one. Its date range sits
# within `DATED_SEGMENT_EDGE_CHARS` of either end: a range in the middle of the
# text belongs to a sentence or to a record wrapped across two parts.
DATED_SEGMENT_MIN_CHARS = 20
DATED_SEGMENT_MAX_CHARS = 200
DATED_SEGMENT_EDGE_CHARS = 12
# A line is a run of records only when its dated parts give at least this many
# different date ranges and make up at least this share of all its parts.
DATED_SEGMENT_MIN_DISTINCT_RANGES = 2
DATED_SEGMENT_MIN_SHARE = 0.5
# A part states a record only with words besides its date range: three letters
# anywhere in the part once the range is cut out. A bare date column fails.
_WORDS_BESIDE_RANGE_RE = re.compile(r"[^\W\d_]{3,}")


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


def _edge_date_range(part: str) -> str | None:
    """The first date range of `part`, in reading order, that starts or ends
    it, or None. When both edges hold one, the range at the start wins: it is
    the earlier match."""
    for match in _DATE_RANGE_RE.finditer(part):
        if match.start() <= DATED_SEGMENT_EDGE_CHARS or len(part) - match.end() <= DATED_SEGMENT_EDGE_CHARS:
            return match.group()
    return None


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
    record. A part's range is the one at its start or end (`_edge_date_range`).
    Each condition removed a case where the recovery pass wrote a detail of one
    record a second time beside its row, in the render of 302 corpus CVs.
    """
    parts = _join_wrapped_parts([part.strip() for part in line.split("\t") if part.strip()])
    candidates = [(part, _edge_date_range(part)) for part in parts]
    dated = [(part, date_range) for part, date_range in candidates
             if date_range and DATED_SEGMENT_MIN_CHARS <= len(part) <= DATED_SEGMENT_MAX_CHARS
             and _WORDS_BESIDE_RANGE_RE.search(_DATE_RANGE_RE.sub(" ", part))]
    distinct = {date_range for _, date_range in dated}
    run_of_records = (len(distinct) >= DATED_SEGMENT_MIN_DISTINCT_RANGES
                      and len(dated) / len(parts) >= DATED_SEGMENT_MIN_SHARE)
    return [part for part, _ in dated] if run_of_records else []


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
