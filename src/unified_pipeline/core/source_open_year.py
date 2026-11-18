"""Whether a CV line writes a year as an open range: "2020-", "6/04-",
"2011.04 - <journal>", "2011 - present" (#946, class 13, #1342).

Moved out of `stage6/formatting/dates.py` unchanged, because two stages ask
the question: stage 6 before it renders "<start>-Present" for a start with
no end, and stage 4's `source_dates.close_unsupported_open_ends` before it
replaces a stored open end the line does not state. One definition
(CODING_STANDARDS §1.5), in `core/` because it is shared by two stages (§2);
stage 4 must not import a stage-6 module.

Pure: a text and a year in, a boolean out. Imports nothing from the pipeline.
"""

import re

# The words that close a range as still open, after the record's own year
# and a dash or "to": "2011 - present", "2011 to current" (decision
# 2026-10-02, class 13). Anchored on the year because a row routinely says
# "now Professor at ..." or names "Current Opinion in ..." about something
# else; a spaced dash followed by text is otherwise a column separator.
_ONGOING_RANGE_END_WORDS = r'(?:present|current|now)\b'

# A two-digit year after a slashed month, or month and day: "6/04-",
# "6/01/04-" (a D1 row in the s7ab batch whose source opens a 2004 start this
# way). The slash is required, so "2021-22" never reads 2022 as written, and
# no digit or slash may precede it, so "2019/06/21-" never reads 2021.
_SLASHED_TWO_DIGIT_YEAR = r'(?<![\d/])\d{{1,2}}/(?:\d{{1,2}}/)?{yy:02d}'

# A month, or a month and a day, written after the year with dots:
# "2014.01", "2014.01.15". A CV that dates its rows this way writes an open
# range "2014.01 - <journal>" (EBYSBC autopsy, class E9: three editorial
# boards read as one-time because the year was not followed by the dash).
_DOTTED_MONTH_DAY = r'(?:\.\d{1,2}){0,2}'


def source_leaves_year_open(source_text: str, year: int | None) -> bool:
    """True when the source writes `year` as an open range: "2020-",
    "(2011-", "11/2019- Clinical ...", "6/01/04-", "2024 -<tab>Member",
    "2014.01- ...", or closes it in words: "2011 - present", "2011 to current", "2011 - now".

    Stage 4 records "2020-" as a start date with no end date, the same as a
    bare "2020", but the author wrote the open dash on purpose -- it is how a
    CV says "since 2020". A dash with a space on both sides and text after it
    ("Award winner, 2023 - Excellence Award") is a column separator, not an
    open range, so it does not count -- except at the very start of the
    entry. "2021 - Member, ..." and "2014.01 - Editorial Board, ..." open
    the entry with the year and a dash: that is the CV's date column saying
    "since 2021" (class E9, EBYSBC autopsy). It also covers "2021 -<tab>",
    whose tab the reader turns into a space before this text is stored, so
    the tab rule above never sees it. A digit after the dash is a closed
    range ("2021 - 2023 Member") and does not count.
    """
    if not source_text or year is None:
        return False
    year_token = (rf'(?:(?<!\d){year}{_DOTTED_MONTH_DAY}|'
                  + _SLASHED_TWO_DIGIT_YEAR.format(yy=year % 100) + ')')
    open_range = re.compile(
        rf'{year_token}(?:[-\u2013\u2014](?!\s*\d)|\s+[-\u2013\u2014][ ]*(?:\t|\)|$)'
        rf'|\s*(?:[-\u2013\u2014]|\bto\b)\s*{_ONGOING_RANGE_END_WORDS})',
        re.MULTILINE | re.IGNORECASE)
    entry_opens_with_open_year = re.compile(
        rf'\A\s*{year_token}\s+[-\u2013\u2014][ \t]*(?=[^\s\d])')
    return bool(open_range.search(source_text)
                or entry_opens_with_open_year.match(source_text))
