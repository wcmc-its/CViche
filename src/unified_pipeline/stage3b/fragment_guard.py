"""T-validation fragment guard for stage 3b (EBYSBC E29, E8; #986, #985).

Stage 3b's T-validation pass (`classify.validate_t_classifications`) asks the
model to move T entries to a specific code, and the model recodes lines that
are not records at all: a wrapped date tail ('<year>- <year>.') becomes an
Honors row, an employer sub-heading becomes a bare Institutional Leadership
row, and its own reasoning says so ("Date fragment ...", "Institution fragment
under Administrative Positions ..."). This module decides, deterministically,
when such a proposal is refused and the entry keeps T (it then reaches
`reconnect_fragments` and the Appendix instead of rendering as a junk row).

Two rules, each narrow:

1. No content words. A line with no word of two or more letters once month
   names, ordinal suffixes and date fillers are set aside ('47.', a year
   range, 'June 2003') carries nothing a section row could show.
2. The model's own reasoning calls the line a fragment, a continuation or a
   header, AND the text is shaped like one: it starts mid-sentence, or it is
   short and does not open with a year. Reasoning that says the line is real
   content "despite being a fragment" does not count, and codes whose rows
   are built from such pieces (positions, grants, degrees, training,
   memberships, contact data) are exempt.

Pure functions; no LLM, no I/O, no stage3b imports.
"""

import re

# Words that are not content: month names and abbreviations, ordinal
# suffixes, and the fillers of a date range ("2001 to present").
_NON_CONTENT_WORDS = frozenset({
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
    "september", "oct", "october", "nov", "november", "dec", "december",
    "st", "nd", "rd", "th", "to", "present", "current", "now", "ongoing",
})
_WORD_RE = re.compile(r"[A-Za-z]+")
# Shortest run of letters that counts as a word ("MD", "PhD" are content).
_MIN_CONTENT_WORD_LETTERS = 2

# The T-validation reasoning calls the line a piece of another record.
_FRAGMENT_REASONING_RE = re.compile(
    r"\bfragment\b|\bcontinuation\b|\bcontinu(?:es|ing)\b|\bsub-?header\b",
    re.IGNORECASE)
# ... or opens by naming the line itself a header ("Header label for ...",
# "Institution name header under ...", "Category header for ..."). Only the
# reasoning's first few words count: "Located under the X section header"
# describes where a real record sits, not what it is.
_HEADER_LEAD_WORDS = 4
# A lead word that places the line under some other header ("Listed under
# reviewer header context") ends the lead: that header is the parent's.
_HEADER_REASONING_RE = re.compile(
    r"^\W*(?:(?!(?:under|in|within|below|beneath|after|listed|located)\b)"
    rf"[\w/'’-]+\s+){{0,{_HEADER_LEAD_WORDS}}}?header\b(?!-)",
    re.IGNORECASE)
# ... unless it says the line is real content anyway ("despite being a
# fragment, this is a presentation title", "despite header formatting").
_NEGATED_REASONING_RE = re.compile(
    r"\b(?:despite|though|although)\s+(?:being\s+)?(?:formatted\s+as\s+)?"
    r"(?:a\s+)?(?:fragment|header)|\bformatted\s+as\s+a\s+fragment\b|header-like",
    re.IGNORECASE)

# Text shape of a fragment: a sentence tail (starts lower-case or with closing
# punctuation) or a line short enough to be a sub-heading or a cut-off piece.
_CONTINUATION_START_RE = re.compile(r"^\s*(?:[a-z]|[)\],;:&])")
_MAX_FRAGMENT_WORDS = 8
# A line that opens with a year ("2000 <committee>, NIH") is the head of a
# dated record, the usual CV row shape, not a piece of one.
_DATED_RECORD_HEAD_RE = re.compile(r"^\s*(?:19|20)\d{2}\b")

# Codes whose rows are built from exactly such pieces, so refusing the recode
# loses content (measured by a render replay over the 63-run farm):
# - D1/D2/D3: stage 6 carries an institution line's employer down to the
#   position rows below it (positions.py `_propagate_institution_to_subentries`);
#   without the header row they inherit an EARLIER, wrong employer.
# - M2*: stage 4 builds a grant record from a wrapped tail line's neighbours,
#   so the tail can hold the only amount, funder and PI the grant shows.
# - A, B1, B2, C, I: a contact line, a degree line, an institution-and-years
#   line, a training line or a society name is itself the row.
_REASONING_RULE_EXEMPT_CODES = frozenset({
    "A", "B1", "B2", "C", "I",
    "D1", "D2", "D3",
    "M2", "M2A", "M2B", "M2C", "M2D",
})

REASON_NO_CONTENT_WORDS = "no content words"
REASON_REASONING_SAYS_FRAGMENT = "reasoning calls it a fragment or header"


def _has_content_word(text: str) -> bool:
    """True when *text* has a word that is not a month, ordinal or date filler."""
    return any(len(word) >= _MIN_CONTENT_WORD_LETTERS
               and word.lower() not in _NON_CONTENT_WORDS
               for word in _WORD_RE.findall(text))


def _reasoning_says_fragment(reasoning: str) -> bool:
    """True when the T-validation reasoning calls the line a fragment,
    continuation or header and does not then argue it is a record anyway."""
    if _NEGATED_REASONING_RE.search(reasoning):
        return False
    return bool(_FRAGMENT_REASONING_RE.search(reasoning)
                or _HEADER_REASONING_RE.search(reasoning))


def _text_is_fragment_shaped(text: str) -> bool:
    """True when *text* starts mid-sentence, or is a short line that does not
    open with a year."""
    if _CONTINUATION_START_RE.match(text):
        return True
    return (len(text.split()) <= _MAX_FRAGMENT_WORDS
            and not _DATED_RECORD_HEAD_RE.match(text))


def t_recode_refusal(text: str, new_code: str, reasoning: str) -> str | None:
    """Why a T-validation recode of *text* to *new_code* is refused, or None.

    *reasoning* is the T-validation model's own reasoning for the recode.
    """
    if not _has_content_word(text):
        return REASON_NO_CONTENT_WORDS
    if (new_code not in _REASONING_RULE_EXEMPT_CODES
            and _reasoning_says_fragment(reasoning)
            and _text_is_fragment_shaped(text)):
        return REASON_REASONING_SAYS_FRAGMENT
    return None
