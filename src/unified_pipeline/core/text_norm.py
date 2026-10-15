"""Text-normalisation helpers shared by the run doctor, the segmentation
regression harness and the render checks."""

import re
import unicodedata

# Substantive-line threshold: shorter lines ("2016", "PhD", bare bullets)
# match by accident and only add noise to the coverage metric.
SUBSTANTIVE_LINE_CHARS = 15


def _fold_marks(text: str) -> str:
    """Drop combining marks ('Müller' -> 'Muller', Greek tonos, Cyrillic
    breve) so an accented word tokenizes the same as its base letters (#541).
    NFKD then NFC: NFC recomposes Hangul jamo so a CJK string keeps its
    character count (CJK is excluded from render tokens, #722). Pure-ASCII input is
    returned untouched, byte-identical."""
    if text.isascii():
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", kept)


def norm(text: str) -> str:
    # Lowercase AFTER folding: NFKD can yield uppercase ("™" -> "TM").
    return _fold_marks(" ".join(str(text or "").split())).lower()


#: Typographic apostrophes and quotes mapped to their ASCII forms. A source
#: page-break header can carry U+2019 where the stage 1a hierarchy node for
#: the same header carries U+0027 ("(CONT'D)", #1232), so a comparison that
#: does not fold them reports a header 1a found as missing.
_QUOTE_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
})


def fold_quotes(text: str) -> str:
    """`text` with typographic apostrophes and double quotes replaced by
    their ASCII forms, so two spellings of one phrase compare equal. Not part
    of `norm`: coverage and render matching already tokenize across
    apostrophes, and widening `norm` would move every metric built on it."""
    return str(text or "").translate(_QUOTE_FOLD)


def squash(text: str) -> str:
    """Whitespace-FREE normalization for the coverage check: stage 2 joins
    text across in-paragraph line breaks with no whitespace at all
    ('Present position:' + break + 'Attending' -> 'position:Attending'),
    and tab-joined label/value lines re-emerge with tabs dropped. Comparing
    with whitespace removed on both sides is immune to all of that."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def looks_like_record(line: str) -> bool:
    line = line.strip()
    return len(line) > 60 and (" | " in line or "\t" in line)


# One date range of a record line: "2009-2011", "1996-8", "5/2017-10/2020",
# "2012-Jun 2015", "2013-present". The start year stops at 2039 so a page
# range ("2064-71") is not read as one.
_DATE_RANGE_RE = re.compile(
    r"\b(?:19\d{2}|20[0-3]\d)\s*[-\u2013\u2014]\s*"
    r"(?:(?:[A-Za-z]{3,9}\.? )?(?:(?:19|20)\d{2}|\d{1,2})\b"
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
# A part states a record only with words beside its date range.
_WORDS_BESIDE_RANGE_RE = re.compile(r"[A-Za-z]{3,}")


def _join_wrapped_parts(parts: list[str]) -> list[str]:
    """`parts` with a part that stage 2 broke inside a parenthesis joined back:
    "... (Supported by" and "Acme Labs, 1998 - 2001)" are one line."""
    joined: list[str] = []
    for part in parts:
        if joined and (joined[-1].count("(") > joined[-1].count(")")
                       or part.count(")") > part.count("(")):
            joined[-1] = f"{joined[-1]} {part}"
        else:
            joined.append(part)
    return joined


def _edge_date_range(part: str) -> str | None:
    """The first date range of `part` that starts or ends it, or None."""
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
    ranges = {part: _edge_date_range(part) for part in parts}
    dated = [part for part in parts
             if ranges[part] and DATED_SEGMENT_MIN_CHARS <= len(part) <= DATED_SEGMENT_MAX_CHARS
             and _WORDS_BESIDE_RANGE_RE.search(_DATE_RANGE_RE.sub(" ", part))]
    distinct = {ranges[part] for part in dated}
    run_of_records = (len(distinct) >= DATED_SEGMENT_MIN_DISTINCT_RANGES
                      and len(dated) / len(parts) >= DATED_SEGMENT_MIN_SHARE)
    return dated if run_of_records else []


#: A whole value in square brackets: "[Title not provided]".
_BRACKETED_RE = re.compile(r"\[[^\[\]]*\]")


def is_placeholder_title(title: object, source_text: str) -> bool:
    """True when `title` is a bracketed placeholder an LLM wrote in place of a
    title the source lacks ("[Title not provided]", "[Title not available]",
    #446: EBYSBC HFAJCC-05, XWNZWW-02). A bracketed value the source itself
    carries ("[AUC]") is the CV's own text, not a placeholder."""
    if not isinstance(title, str):
        return False
    value = title.strip().rstrip(".").strip()
    return bool(_BRACKETED_RE.fullmatch(value)) and norm(value) not in norm(source_text)
