"""The century a two-digit year ("03", "'99", "5/1/17") reads as.

Shared by stage 6, which reads a raw mm/dd/yy string with it (#867), and
stage 4, which re-derives the century of a year the LLM extracted from a
two-digit source token and put in the wrong century. One definition, so the
two stages cannot disagree about what "03" means.
"""

# yy <= this reads as 20yy, above it as 19yy. A constant rather than the
# clock (CODING_STANDARDS §7.4). CV dates are almost all past events, so it
# sits well below strptime's 68.
TWO_DIGIT_YEAR_PIVOT = 30


def expand_two_digit_year(yy: int) -> int:
    """The four-digit year that the two-digit year `yy` (0-99) reads as."""
    return (2000 if yy <= TWO_DIGIT_YEAR_PIVOT else 1900) + yy
