"""Section T: the appendix -- content that reached no other section (#398).

Not a WCM template section in the sense the others are: T is appended to the end
of the document, and everything in it is a placement failure the pipeline is
choosing to admit to rather than swallow. The section exists so that a
classification miss costs the reader a scroll instead of costing them the
content, and its formatting deliberately mirrors S. BIBLIOGRAPHY so it reads as
part of the document.

The work is split along its natural boundary (review on #736). The module-level
functions decide *what* the appendix holds and are pure over the entry dicts:
`_filter_unmapped_entries` (which entries survive, and why each of the rest did
not), `_group_by_source_heading` (the appendix's model: heading -> numbered
lines), `_truncate_appendix_text` and `_describe_dropped`. `AppendixSection`'s
methods only write that model into the Word document. The rules are therefore
testable without a document, and the writer carries no rule of its own.

Several checks run before anything is written, in `_appendix_drop_reason`'s
order. Every drop is counted under the check that caught it, so the summary
comment and the log line say what was removed instead of calling every drop
"boilerplate":

- blank text;
- WCM template instructions and source-CV furniture (title pages, date stamps
  -- #213). These reach the unmapped pile precisely because they are not CV
  content, and letting them through produced the large spurious appendix dumps
  `core.template_boilerplate` was written to stop;
- entries that render to nothing once the readers' cell separators are collapsed
  by `_clean_inline_tabs`. A blank template table row ("|  |  |") is non-empty as
  raw text and empty on the page. Tested before the header-row check below,
  which would otherwise claim it (a row with no words is trivially "all
  column-label vocabulary");
- table column-header rows that reached the unmapped pile T-coded (#424):
  `_is_column_header_row` (`..render_check`) flags a row as a header when at
  least half its words are column-label vocabulary (title, institution, role,
  name, date, ...), catching shapes like "Year: Degree | Discipline |
  Institution/Location" and bare "NAME:" before they reach the appendix as a
  spurious numbered line and shift the numbering of the genuine entries after
  it.

The first four checks are precision-biased -- an entry that is merely suspicious
survives. The header-row check is not strictly so: a short entry at least half
of whose words (digits count as words) are column-label vocabulary (a two-word
"Committee Chair" T-coded row, "Date: 2019") trips the same vocabulary-majority
heuristic and is dropped, a known false-positive class the 66-CV corpus does not
currently exercise. `test_stage6_appendix_header_row_filter.py` pins the real
header shapes, the numeric-token and short-entry negatives, and that
false-positive class, so a change to the heuristic is visible.

Three more checks (#885) catch structural scaffolding that is specific to T:
a bare year ("2021"), a lone status word ("Completed", "In Press") and a
source CV's own table-of-contents line ("Honors and Awards       Page 7").
The issue's own filed figure -- T-validation-confirmed structural entries are
about 79% of that batch's Appendix lines -- covers EVERY shape stage 3b's
`classification_reasoning` calls structural (also section headers, date
stamps, CV titles, and table-header rows the pattern above already catches);
these three checks are a safe subset of that, not all of it. A render A/B on
the issue's own 20-CV batch (dev vs. this fix, over the farm's cached stage
output for determinism, `T. APPENDIX` numbered-line count) shows what these
three checks alone remove: about 30% of that batch's Appendix lines (65 of
219). The remaining lines -- section headers, date stamps, CV titles, and
other T-validation-confirmed shapes these three checks do not match -- were
that change's disclosed residual, handled by the two-signal check described
after this paragraph. Stage 3b's own T-validation pass already flags the shapes these checks
remove in free text ("[T-validation confirmed] Bare year '2021' is a
structural marker"), but that string is free LLM text, not a drop predicate:
the SAME tag also covers real, correctly-T-coded content (hobbies, a
career-gap explanation, a skills list) that must not be dropped, so
`_appendix_drop_reason` never reads it. Each of the three checks instead
matches the entry's raw TEXT shape, independent of any reasoning string, and
-- unlike every check above -- is gated to `taxonomy_code == "T"`: measured
against the full 126-CV #885 corpus (37,704 entries, every taxonomy code),
none of the three shapes ever occurs outside T, so the gate is defense in
depth, not the thing doing the precision work. See the comments above
`_BARE_YEAR_RE`, `_STATUS_MARKER_WORDS` and `_TOC_LINE_RE`.

The residual (#885) is closed for four shapes by `_confirmed_structural_reason`,
which, unlike the three checks above, DOES read `classification_reasoning` --
but never alone. An entry is dropped only when it is T-coded, stage 3b's
`[T-validation confirmed]` reasoning names the kind in its first
`_REASONING_LEAD_CHARS` characters (a section header, a document title, a date
stamp, a table header row), AND the raw text has that kind's positive shape:
a short digit-free label (`_is_section_label_text`), a "Curriculum Vitae"
title (`_CV_TITLE_RE`), "Date/As of/Revised <date>" (`_DATE_STAMP_RE`), or a
multi-cell digit-free row (`_is_label_only_row`). Either signal alone keeps the
entry, so hobbies, reference lists, orphaned journal titles and organisation
names that carry the same confirmation tag survive. It runs last, so no older
check loses a drop to it. Reasons: `cv-title`, `date-stamp`, `section-header`,
and the existing `column-header`.

Another institution's template adds two shapes the same two-signal rule now
covers (#530). A bare OUTLINE label ("1. Honors or Awards", "b. National:")
failed the digit-free label shape on the marker's own digit; one leading marker
is now stripped first (`_OUTLINE_MARKER_RE`), and stage 3b's "header/category
label" and "subsection marker" wordings count as the section-header verdict.
A marked label's closing parenthetical ("(e.g., ...)", "(... if applicable)")
is the template's qualifier and does not count toward the label's word cap.
A wrapped instruction tail ("...reverse chronological order)", "use underline
or bold font for your name") is dropped as `template-instruction` when the
reasoning calls it instruction text and the text closes a parenthesis it never
opened or addresses the author (`_is_wrapped_instruction_tail`).

Three more shapes close #1221. A rule line (underscores, hyphens, equals signs
or dashes, five or more) is dropped on its text alone (`_RULE_LINE_RE`, T-only
like `_BARE_YEAR_RE`). A signature block ("(Date) (Signature of Candidate)",
"Signed: <owner>", a date beside the owner's name) is dropped as
`signature-block` only when 3b's reasoning says signature, signed, footer or
structural artifact AND `_is_signature_block` finds nothing in the text but
dates, blanks, the owner's own name tokens (`_owner_signature_tokens`, read from
the `cv_owner` stage 6 already holds) and the words Date, Signature, Signed and
the phrase "Signature of Candidate", with at least one of those words present. `_DATE_STAMP_RE` now also takes a numeric date, parentheses,
and a full date alone on its line.

Page furniture is the last T check (#1221, EBYSBC class E24): the source's
running headers and footers, its Word field-code text, a lone "Page", revision
stamps and "<SECTION> (Continued):" headers (`is_page_furniture`), plus a
"References available on request" line (`_ON_REQUEST_RE`). Like the rule line
these match on text alone, because 3b's wording for them varies too much to key
on, and the shape is strict: once field-code instructions, page numbers, dates,
the owner's own name tokens and a small furniture vocabulary are removed,
nothing may be left, and the line must have carried a field code, a page, CV or
revision word, or the owner's name. A line with any other word in it is kept,
and so is one holding a citation's volume and pages, a phone number or a number
of five digits or more.
Two two-signal shapes widen with it: a column-header row whose cells are
separated by single spaces (`_is_label_only_row`), and a label followed only by
a parenthesised directive ("Sample Statement: (to be completed by ...)").

Two shapes no longer wait for 3b's wording (#530, RCBKFG UYFRTL 17, GKAQHB 41).
The rule line takes any run of five or more symbols with no letter, digit or
space ("*****"). A bare label that closes with a colon and holds nothing after it
("Sample Background:") is dropped as `section-header` on its text alone
(`_is_bare_section_label`) when it is the only line of its Appendix group
(`_drop_lone_bare_labels`); beside other lines it is their lead-in and stays.
The two-signal rule kept it whenever 3b called it a "header label only" rather
than a section header.

The same furniture test keeps an A-coded orphan out of the post-render recovery
(stage 6's `_unconsumed_personal_data_batch`), and decides the severity of the
"A ... recovered into the Appendix" warning: INFO when every recovered A line is
furniture, a school or affiliation line, a profile URL or a label left bare once
its value was withheld (`is_routine_recovered_line`), WARN otherwise.

What was dropped is reported ONCE, as a single Word comment on the introductory
paragraph, rather than per entry -- N comments saying so is itself noise.

Entries are grouped under the source CV heading they were found beneath, since
"From ..." is usually enough for a reader to see what the pipeline missed and
where it belonged. The key is the top-level heading text (`hierarchy[0]`), by
design: the label the reader sees IS the heading text, so two source sections
sharing a heading would be indistinguishable as separate groups anyway. That
key is NOT proven collision-free: measured 2026-09-05 over the local corpus,
3 of 111 stage-1b files carry a duplicated top-level heading (one, 2025_Denckla's
"Conference Activities", at two genuinely distinct source positions -- the
merge-and-renumber scenario this key risks). Only one of the three,
2068_Yount ("GRANT SUPPORT", two synthetic nodes), is among the 66 stage-6
inputs, and its current appendix has a single entry under that heading, so
`_group_by_source_heading` has not yet been exercised on a real collision.
Numbering restarts under each heading. Bodies of entries diverted here from
another code are capped at `APPENDIX_MAX_CHARS` characters, the marker that
shows the cut included: such an appendix line is a pointer back to the original
document, not a second copy of it. A T-coded body is NOT capped
(`AppendixSection._appendix_body`, #1230) unless the low-coverage overflow
re-splits it: the Appendix is the only place a T entry renders, so its cut
tail reached no page. The cut lost undated lines the recovery pass
cannot tell from template text (a competency list, a duty bullet) and sibling
rows that differ from a kept one only in a short word or a digit, and where
recovery did re-add a cut line, the entry printed twice: cut, then whole.
"""
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple, TypedDict

from ...core.template_boilerplate import (
    is_foreign_template_instruction,
    is_near_template_instruction,
    is_source_boilerplate,
    is_template_instruction,
    is_template_label_line,
    is_unanswered_prompt,
)
from ..formatting import _set_font, add_cviche_box, cviche_box_line, is_cviche_box
from ..normalization import _clean_inline_tabs
from ..normalization.pii import _BARE_EMAIL_SHAPE, _BARE_PHONE_SHAPE
from ..render_check import _is_column_header_row

if TYPE_CHECKING:
    from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

# Ceiling on a rendered appendix body, INCLUDING the marker that shows a cut
# was made -- a line the reader sees is never longer than this. The old inline
# `text[:200] + '...'` produced 203-character lines against a documented 200
# (review on #736).
APPENDIX_MAX_CHARS = 200
_TRUNCATION_MARKER = "..."

# Group label for an entry that carries no source hierarchy at all.
_UNKNOWN_SECTION = "Unknown Section"

# The CViche note box under "T. APPENDIX", written by both appendix writers
# (#1388): what the entries are and what to do with them. It must not claim
# the entries appear nowhere else: stage 6 cannot prove that (#534 -- some
# numbered lines repeat content a section rendered, and some carry content a
# renderer dropped). The count is the final one, set once both writers ran.
APPENDIX_NOTE_TITLE = "CViche note: delete this box before sending"
APPENDIX_NOTE_TEXT = {
    True: ("This entry from your original CV did not fit any section above. Move it to "
           "the right section or delete it. It may already appear above."),
    False: ("These {count} entries from your original CV did not fit any section above. "
            "For each one, move it to the right section or delete it. Some may already "
            "appear above."),
}


_SOURCE_SECTION_NUMBER_RE = re.compile(r"^(?:[IVXLCDM]+|[A-Z]|\d+)[.)]\s+")
# Words an Appendix group heading keeps lower-case in Title Case, after its first.
_HEADING_MINOR_WORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"})


def appendix_group_heading(heading: str) -> str:
    """The source CV's section heading as an Appendix group heading: its own
    words, without its section number, in Title Case when it was ALL CAPS
    ("V. GRANT SUPPORT" -> "Grant Support"). An ALL-CAPS line reads as a WCM section header, to a reader
    and to the doctor's `_output_section_header`, so it would seem to end the
    Appendix (#1388)."""
    # The source's own section number ("V. ", "XI. ", "3) ") reads as a WCM
    # section letter ("A. ") beside the Appendix's other headings: drop it.
    heading = _SOURCE_SECTION_NUMBER_RE.sub("", heading).strip() or heading
    if heading != heading.upper():
        return heading
    parts = re.split(r"([\s/-]+)", heading.lower())  # words, and what separates them
    return "".join(w if i and w in _HEADING_MINOR_WORDS else w[:1].upper() + w[1:]
                   for i, w in enumerate(parts))


def appendix_note_text(count: int) -> str:
    """The Appendix note box's instruction for *count* entries."""
    return APPENDIX_NOTE_TEXT[count == 1].format(count=count)

# Why an entry did not reach the appendix, named for the check that caught it.
# These are the words the summary Word comment and the log line report, in
# `DROP_REASONS` order, so a reader can tell two dropped header rows from two
# dropped blanks (review on #736: one undifferentiated count called every
# drop "boilerplate/empty").
DROP_BLANK = "blank"
DROP_TEMPLATE_INSTRUCTION = "template-instruction"
DROP_SOURCE_BOILERPLATE = "source-boilerplate"
DROP_RENDERS_EMPTY = "renders-empty"
DROP_COLUMN_HEADER = "column-header"
DROP_BARE_YEAR = "bare-year"
DROP_STATUS_MARKER = "status-marker"
DROP_TOC_LINE = "toc-line"
DROP_NEAR_TEMPLATE_INSTRUCTION = "near-template-instruction"
DROP_UNANSWERED_PROMPT = "unanswered-prompt"
DROP_TEMPLATE_LABEL = "template-label"
DROP_CV_TITLE = "cv-title"
DROP_DATE_STAMP = "date-stamp"
DROP_SECTION_HEADER = "section-header"
DROP_RULE_LINE = "rule-line"
DROP_SIGNATURE_BLOCK = "signature-block"
DROP_PAGE_FURNITURE = "page-furniture"
DROP_ON_REQUEST = "on-request"
DROP_REASONS = (
    DROP_BLANK,
    DROP_TEMPLATE_INSTRUCTION,
    DROP_SOURCE_BOILERPLATE,
    DROP_RENDERS_EMPTY,
    DROP_COLUMN_HEADER,
    DROP_BARE_YEAR,
    DROP_STATUS_MARKER,
    DROP_TOC_LINE,
    DROP_NEAR_TEMPLATE_INSTRUCTION,
    DROP_UNANSWERED_PROMPT,
    DROP_TEMPLATE_LABEL,
    DROP_CV_TITLE,
    DROP_DATE_STAMP,
    DROP_SECTION_HEADER,
    DROP_RULE_LINE,
    DROP_SIGNATURE_BLOCK,
    DROP_PAGE_FURNITURE,
    DROP_ON_REQUEST,
)

# The taxonomy code this whole module exists for (CODING_STANDARDS.md 8.2:
# a comparison on a taxonomy code reads through a named constant). Named
# despite being the module's own subject -- spelled out as a literal
# elsewhere in this file ("T. APPENDIX", `_write_appendix_intro`) -- because
# the #885 gate below is a CLASSIFICATION decision on the code, the shape
# 8.2 names as its canonical instance, not a display string.
_APPENDIX_TAXONOMY_CODE = "T"

# #885: three T-only structural shapes -- see the module docstring's "Three
# more checks" paragraph for why these match the entry's raw TEXT rather than
# its (untrustworthy as a predicate) `classification_reasoning` string.

# A bare four-digit year, optionally with one trailing period ("2021",
# "2016.") -- a year with nothing else attached carries no information a
# reader could act on. Matched on 323 T entries across the #885 corpus (126
# CVs, 37,704 entries of every taxonomy code) and zero non-T entries.
_BARE_YEAR_RE = re.compile(r"^(?:19|20)\d{2}\.?$")

# A single status word with no subject, title, or venue attached -- "a
# specific ... entry" (the #885 issue's own phrase) always carries more than
# just its status. Exact, case-insensitive, whole-string match (not
# "contains") against the shapes actually observed: web210's presentation
# statuses, web226's grant statuses. Matched on 7 T entries, zero non-T.
_STATUS_MARKER_WORDS = frozenset({
    "completed", "scheduled", "pending", "ongoing", "active", "current",
    "funded", "not funded", "withdrawn", "submitted", "in press", "published",
})

# A source CV's own table of contents: heading text, then a run of
# whitespace (the dot leader Word draws between a ToC entry and its page
# number, collapsed by the reader -- as little as the single space in
# "...Extramural Presentations Page 17"), then "Page N" or "Page N-M".
# Matched on all 18 ToC lines in web181, the CV #885 was filed against, and
# zero other entries in the #885 corpus.
_TOC_LINE_RE = re.compile(r"^.{1,90}?[ \t]Page\s+\d+(?:[-–—]\d+)?\s*$")

# #1221: a rule line -- nothing but underscores, hyphens, equals signs or
# dashes, five or more of them. Text-only like `_BARE_YEAR_RE`: a rule is not
# content under any code, so no reasoning signal is needed.
#
# #530 (RCBKFG GKAQHB 41): any other run of five or more symbols with no
# letter, digit or space ("*****", "#####", "~~~~~") is a separator of the
# same kind. A spaced run ("_ _ _ _ _") is a fill-in blank and is kept.
#
# #1431 (NDMRSO JJYBKP 5): a dot leader keeps its spaces (". . . . .",
# "· · · · ·"), so five or more dots are a separator spaced or not. Other spaced
# symbols ("* * * * *") are still kept, and so is a spaced underscore run.
_LEADER_DOTS = ".·•∙⋅…"
_RULE_LINE_RE = re.compile(
    rf"^(?:[^\w\s]|_){{5,}}$|^[{_LEADER_DOTS}](?:[ \t]*[{_LEADER_DOTS}]){{4,}}$")


# #885 residual: four structural shapes that need TWO independent signals, so
# neither the free-text reasoning nor the raw text carries the drop alone.
# Signal 1: stage 3b's T-validation pass confirmed the entry structural AND
# its reasoning names the kind in its opening words. Signal 2: the entry's raw
# TEXT has the positive shape of that kind. "[T-validation confirmed]" alone
# also tags real T content (hobbies, reference lists, orphaned journal titles),
# which is why it is never sufficient here.
_T_CONFIRMED_PREFIX = "[T-validation confirmed]"
# #1431: 3b's T-validation can also move a structural line OUT of T, into the
# section it heads ("[T-validation reclassified from T] Table header for ...",
# stage3b/classify.py). Such a line reaches the Appendix under its new code, and
# the same two signals drop it there.
_T_RECLASSIFIED_PREFIX = "[T-validation reclassified from T]"
# How much of the reasoning, after the tag, may name the kind. The model
# states the verdict first ("Section header 'X' ..."), so a kind word deep in
# the prose (an explanation of why something is NOT a header) does not count.
_REASONING_LEAD_CHARS = 100

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTH_YEAR = rf"{_MONTH}\.?,?\s*(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s*)?(?:19|20)\d{{2}}"

# "Date May 30th, 2020", "As of August 10, 2022", "*Revised June 29, 2018":
# a stamp word, then nothing but a date.
#
# #1221: the date may also be numeric ("(As of 02/14/2017)"), the stamp may sit
# in parentheses, and a FULL date (day included) alone on its line is a stamp
# without its word. A bare month-year ("May 2020") is not: that shape is also
# a real record's date fragment.
#
# #1431: a slashed date may have a two-digit year ("7/16/18", NDMRSO VYNARH
# 658; with dots or hyphens a two-digit year is still read as a section number
# or a range), and the day may come first ("As of 21 April 2023", CAGLNY 2).
_NUMERIC_DATE = r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}/\d{1,2}/\d{2})"
_DAY_FIRST_DATE = rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s*(?:19|20)\d{{2}}"
_FULL_DATE = (rf"(?:{_MONTH}\.?,?\s*\d{{1,2}}(?:st|nd|rd|th)?,?\s*(?:19|20)\d{{2}}"
              rf"|{_DAY_FIRST_DATE}|{_NUMERIC_DATE})")
_STAMP_WORD = r"(?:date|as\s+of|revised|last\s+updated|updated|revision\s+date)"
_DATE_STAMP_RE = re.compile(
    rf"^\*?\(?\s*{_STAMP_WORD}\s*:?\s*(?:{_MONTH_YEAR}|{_FULL_DATE})\s*\)?$"
    rf"|^\(?\s*{_FULL_DATE}\s*\)?$",
    re.IGNORECASE,
)

# #1221: a signature block ("(Date) (Signature of Candidate)", "Signed: <owner>",
# a date beside the owner's name). Signal 1 is 3b's wording, anywhere in the
# lead; signal 2 is `_is_signature_block`'s shape.
# Word-bounded, so "assigned" and "designed" are not "signed", and a negated
# phrase ("not a structural artifact") is not a signal.
_SIGNATURE_REASONING_RE = re.compile(
    r"(?<!not )(?<!not a )\b(?:signature|signed|footer|structural artifact)\b"
)
# "Candidate" and "of" count only inside this phrase, so "PhD Candidate, May 2019"
# keeps its word "candidate" and is not a signature block.
_SIGNATURE_PHRASE_RE = re.compile(r"\bsignature\s+of\s+candidate\b")
_SIGNATURE_VOCAB = frozenset({"date", "signature", "signed"})
_SIGNATURE_DATE_RE = re.compile(rf"{_FULL_DATE}|{_MONTH_YEAR}", re.IGNORECASE)
_BLANK_RUN_RE = re.compile(r"_+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_OWNER_NAME_FIELDS = ("first_name", "middle_name", "last_name")

# The CV's own title, up to two leading words ("Medico-legal Curriculum
# Vitae"), an optional "& Bibliography", and optionally a date line under it.
_CV_TITLE_RE = re.compile(
    r"^(?:[\w.\-]+\s+){0,2}curriculum\s+vit(?:ae|a)"
    r"(?:\s*(?:&|and)\s*bibliography)?"
    rf"(?:\s*\n\s*(?:{_MONTH}\.?,?\s*)?(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s*)?(?:19|20)\d{{2}})?$",
    re.IGNORECASE,
)

_SECTION_HEADER_MAX_WORDS = 10
_NONE_WORD_RE = re.compile(r"\bnone\b", re.IGNORECASE)
_HEADER_CELL_SPLIT_RE = re.compile(r"\s*\|\s*|\t+|\n|\s{2,}")
_HEADER_ROW_MAX_CELL_WORDS = 8

# How stage 3b's reasoning names each structural kind, in its opening words.
_CV_TITLE_REASONING_RE = re.compile(r"(?:document|cv|curriculum vitae)[^.;]{0,25}(?:title|header)")
# #1431: "Stray date fragment with no context" (NDMRSO VYNARH 658).
_DATE_STAMP_REASONING_RE = re.compile(r"date (?:stamp|line|marker|fragment)|revision date|timestamp")
# #1431: 3b dating the CV itself ("likely corresponds to CV date/preparation
# date", NDMRSO REOYVH 0). Only this verdict lets a month and year alone drop:
# under a plain "date stamp" verdict "May 2020" may be a record's date fragment.
_CV_DATE_REASONING_RE = re.compile(r"\bcv date|preparation date|date of (?:the )?(?:cv|resume)\b")
# "column labels": stage 3b's "header line with only column labels" (#1221).
# #1431: "Table header for graduate student ... awards" (NDMRSO CAGLNY 688).
_COLUMN_HEADER_REASONING_RE = re.compile(
    r"(?:column|table)?\s*header row|column header|column labels?\b|table header\b")
_SECTION_HEADER_REASONING_RE = re.compile(
    # The verdict must be a SECTION header/label, not a looser
    # "structural header" (which also described a name line, an
    # institution name and a career-gap explanation in the corpus).
    r"(?:sub)?section (?:header|heading|subheader|subheading|category|label|title)"
    # Stage 3b's other wordings for the same verdict on a bare outline
    # label (#530): "structural header/category label", "subsection marker".
    r"|header/category label|subsection marker"
)
_TEMPLATE_INSTRUCTION_REASONING_RE = re.compile(r"instruction (?:text|line|placeholder)|broken header")
# #1431: "Continuation header for Peer-Reviewed Publications section" (NDMRSO
# UYQRUN 679), on an entry 3b moved out of T into the section it continues.
_CONTINUATION_REASONING_RE = re.compile(r"continu(?:ation|ed) (?:header|heading)")


# The outline marker a template puts before a label ("1. ", "b. ", "iv. ",
# "3) "). Stripped (the ^ anchor makes it once) before the digit-free test so
# "1. Honors or Awards" reads as the label "Honors or Awards" (#530); a digit
# anywhere else still means data.
_OUTLINE_MARKER_RE = re.compile(r"^(?:\d{1,2}|[A-Za-z]|[ivxIVX]{1,4})[.,)]\s+")
_LABEL_ETC = ", etc."
_ETC = "etc."
# The parenthetical that closes an OUTLINE-MARKED label is the template's
# qualifier on it ("3. Sample Placement (e.g., ... if applicable)"), not part of
# the label, so it does not count toward the word cap (#530). It is still
# checked for digits and "none" with the rest of the line.
_TRAILING_QUALIFIER_RE = re.compile(r"\([^()]*\)$")


def _is_section_label_text(text: str) -> bool:
    """A short, digit-free, non-sentence line: what a section heading looks
    like on the page. Trailing colon allowed; a terminal period is a sentence
    unless it closes "etc." (a category label: "Professional Societies, etc.").
    One leading outline marker ("1.", "b.") is not part of the label, nor is
    the parenthetical qualifier that ends a marked label."""
    stripped = text.strip()
    body = _OUTLINE_MARKER_RE.sub("", stripped)
    if not body or any(ch.isdigit() for ch in body):
        return False
    if body.endswith(".") and not body.lower().endswith(_LABEL_ETC):
        return False
    if _NONE_WORD_RE.search(body):  # "Patents (none)": says something about content
        return False
    if body != stripped:
        body = _TRAILING_QUALIFIER_RE.sub("", body) or body
    return len(body.split()) <= _SECTION_HEADER_MAX_WORDS


# A header row whose cells reached stage 6 joined by single spaces ("Project
# Sponsor Role Percent Period", #1221): one line of capitalised words, with only
# connectors or symbols between them.
_LABEL_RUN_MIN_WORDS = 3
_LABEL_RUN_MAX_WORDS = 12
_LABEL_RUN_CONNECTORS = frozenset({"of", "and", "or", "by", "in", "for", "the", "to", "&", "#", "/", "-"})
_SENTENCE_PUNCTUATION = ".,;:"


def _is_heading_words(words: Sequence[str]) -> bool:
    """Every word is capitalised or a connector, as in a heading or a label."""
    return all(word[0].isupper() or word.lower() in _LABEL_RUN_CONNECTORS for word in words)


def _is_label_run(text: str) -> bool:
    """One line of three to twelve words, each capitalised or a connector, with
    no sentence punctuation: a header row's labels side by side."""
    words = text.split()
    if "\n" in text or not _LABEL_RUN_MIN_WORDS <= len(words) <= _LABEL_RUN_MAX_WORDS:
        return False
    if any(ch in text for ch in _SENTENCE_PUNCTUATION):
        return False
    capitalised = [word for word in words if word[0].isupper()]
    return len(capitalised) >= _LABEL_RUN_MIN_WORDS and _is_heading_words(words)


def _is_label_only_row(text: str) -> bool:
    """A multi-cell row whose every cell is a short, digit-free label, or one
    line of labels (`_is_label_run`) -- the shape of a table header the
    vocabulary heuristic (`_is_column_header_row`) missed. Any digit means data
    (a year, a count), so the row is kept."""
    if any(ch.isdigit() for ch in text):
        return False
    cells = [c for c in _HEADER_CELL_SPLIT_RE.split(text) if c and c.strip()]
    if len(cells) < 2:
        return _is_label_run(text)
    return all(len(c.split()) <= _HEADER_ROW_MAX_CELL_WORDS for c in cells)


# A wrapped instruction tail: the second line of a template's own directive
# ("...preferably in reverse chronological order)", "presenter, etc.)"), or a
# directive that addresses the author ("use underline or bold font for your
# name"). Digit-free and short, so a wrapped real record (which carries a
# year, volume or page) never matches (#530).
_WRAPPED_TAIL_MAX_WORDS = 25
_AUTHOR_DIRECTIVE_RE = re.compile(
    r"\b(?:use|please|list|include|provide)\b[^;.]{0,40}\byour\b", re.IGNORECASE)


def _is_wrapped_instruction_tail(text: str) -> bool:
    """A short digit-free line that closes a parenthesis it never opened, or
    tells the author what to do (#530)."""
    body = text.strip()
    if not body or any(ch.isdigit() for ch in body):
        return False
    if len(body.split()) > _WRAPPED_TAIL_MAX_WORDS:
        return False
    closes_unopened = body.endswith(")") and body.count(")") > body.count("(")
    return closes_unopened or bool(_AUTHOR_DIRECTIVE_RE.search(body))


# A form label followed only by the template's own directive in parentheses
# ("Sample Statement:  (to be completed by the applicant; please be brief.)",
# #1221).
# Digit-free, so a label holding a real value in parentheses is kept.
_LABELLED_DIRECTIVE_RE = re.compile(
    r"^[^()\d:]{1,60}:\s*\(\s*(?:to\s+be\s+(?:written|completed|provided|filled)"
    r"|please|provide|describe|list|include|limit)\b[^()\d]*\)\.?$",
    re.IGNORECASE,
)


def _is_template_directive(text: str) -> bool:
    """A wrapped instruction tail (#530) or a labelled directive (#1221)."""
    return _is_wrapped_instruction_tail(text) or bool(_LABELLED_DIRECTIVE_RE.match(text.strip()))


class OwnerTokens(NamedTuple):
    """The owner's word tokens for the signature-block and page-furniture
    shapes (#1221).

    `removable` is everything a shape may subtract from a line: name words,
    credentials ("MD", "FACP") and single initials. They are only subtracted, never
    evidence: a signature block needs a Date, Signature or Signed word.

    `names` (the first, middle and last name words of two letters or more) and
    `initials` (the first letter of each of those words, however short) let the
    page-furniture shape count the owner's name as evidence that a line is a
    running header, alone or written behind up to three initials ("JQDoe")."""

    removable: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    initials: frozenset[str] = frozenset()


# No owner name known: the shapes that read the owner's tokens match nothing.
_NO_OWNER_TOKENS = OwnerTokens()


def _name_words(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return set(_WORD_RE.findall(value.replace(".", "").lower()))


def _owner_signature_tokens(cv_owner: Mapping[str, object] | None) -> OwnerTokens:
    """Lower-cased word tokens of the owner name stage 6 already carries in
    `cv_owner` (periods dropped, so "D.V.M." and "DVM" agree), plus the initial
    of each (#1221)."""
    owner = cv_owner or {}
    removable = _name_words(owner.get("full_name_with_credentials"))
    name_words: set[str] = set()
    for field in _OWNER_NAME_FIELDS:
        name_words |= _name_words(owner.get(field))
    removable |= name_words
    # A name written with a middle initial ("Jane Q. Doe" as "J. Doe") still has the
    # owner's tokens: an initial of any owner token counts as one.
    removable |= {token[0] for token in removable}
    return OwnerTokens(
        frozenset(removable),
        frozenset(word for word in name_words if len(word) > 1),
        frozenset(word[0] for word in name_words),
    )


def _is_signature_block(text: str, owner_tokens: OwnerTokens) -> bool:
    """True when, once dates, blanks and the owner's name tokens are removed,
    nothing is left but the signature vocabulary, and at least one Date,
    Signature or Signed word remains (#1221). A real record carries some other
    word, so it never matches, and a name plus a date alone is not enough
    ("PhD, May 2019" is a degree record)."""
    body = _SIGNATURE_PHRASE_RE.sub("signature", text.lower())
    body = _SIGNATURE_DATE_RE.sub(" ", _BLANK_RUN_RE.sub(" ", body))
    words = _WORD_RE.findall(body.replace(".", ""))
    rest = [w for w in words if w not in owner_tokens.removable]
    return bool(rest) and all(w in _SIGNATURE_VOCAB for w in rest)


# #1221 (EBYSBC E24): page furniture. The source's running headers and footers,
# its page-number and date fields, a lone "Page" and its revision stamps reach
# the Appendix T-coded, under reasoning too varied to key on. None of it is CV
# content under any code, so `is_page_furniture` matches on text alone.
#
# Word field-code instruction text a .doc conversion leaves in the runs as
# literal text: a switch with its argument ('\* MERGEFORMAT', '\@ "MMMM d,
# yyyy"', '\h', '\r 1') ...
_FIELD_SWITCH_RE = re.compile(r'\\[*@#!]\s*(?:"[^"]*"|[A-Za-z]+)?|\\[a-z]\b(?:\s+\d+)?')
# ... the field names, which Word writes in capitals (so "Page" or "Date" as a
# word is not one), removed once the line holds a field code ...
_FIELD_NAME_RE = re.compile(r"\b(?:PAGE|NUMPAGES|SECTIONPAGES|MERGEFORMAT|SEQ|SHAPE|DATE|CHAPTER)\b")
# ... and the names that are a field code even without a switch ("Page PAGE 14").
_SWITCHLESS_FIELD_NAME_RE = re.compile(r"\b(?:PAGE|NUMPAGES|SECTIONPAGES|MERGEFORMAT)\b")
# "P a g e": a PDF header's letter-spaced word.
_SPACED_PAGE_RE = re.compile(r"\bp\s+a\s+g\s+e\b", re.IGNORECASE)
_POSSESSIVE_RE = re.compile(r"['’]s\b")
# A page number, arabic, ordinal or roman ("26", "3rd", "iv"), or a date's day,
# month or year once its separators are gone. Four digits at most, so a ZIP code
# or a phone number written without separators is not one.
_PAGE_NUMBER_RE = re.compile(r"\d{1,4}(?:st|nd|rd|th)?|[ivx]{1,4}")
# Numbers no page furniture holds: a citation's volume, issue and pages
# ("2011;7:101-109") or a phone number. A line with either is record content.
_RECORD_NUMBER_RE = re.compile(r"\d\s*[;:]\s*\d|" + _BARE_PHONE_SHAPE)
# Words that show a line is furniture: a page, the CV's title, a revision stamp.
# Not "pages" or "pg": "Pages 12-19" is a citation's page range.
# #1431: "resume" too ("Date of this résumé: February 9, 2009", NDMRSO JJYBKP
# 516), read with its accents folded (`_fold_accents`).
_FURNITURE_EVIDENCE_WORDS = frozenset({
    "page", "cv", "curriculum", "vitae", "vita", "resume",
    "revised", "revision", "updated", "update", "version", "prepared",
})
# Words such a line may also hold, which are no evidence by themselves.
_FURNITURE_FILLER_WORDS = frozenset({
    "of", "date", "dated", "last", "as", "on", "name", "p", "this",
    "january", "jan", "february", "feb", "march", "mar", "april", "apr", "may",
    "june", "jun", "july", "jul", "august", "aug", "september", "sep", "sept",
    "october", "oct", "november", "nov", "december", "dec",
})
# "SAMPLE APPOINTMENTS (Continued):": the label a section repeats at the top of
# each page it runs onto. The label must read as a heading (`_is_continued_header`):
# a few capitalised words, no comma or sentence, so a record that ends
# "(continued)" is kept.
_CONTINUED_HEADER_RE = re.compile(
    r"^(?P<label>[^\d()]{2,80})\(\s*(?:continued|cont(?:'d|’d|\.)?)\s*\)\s*:?$", re.IGNORECASE)
_CONTINUED_LABEL_MAX_WORDS = 6
# "References: Available on Request" (the label may sit in another cell).
_ON_REQUEST_RE = re.compile(
    r"^(?:references?\s*:?\s*)?(?:are\s+)?available\s+(?:up)?on\s+request\.?$", re.IGNORECASE)
# How many of the owner's initials a running header runs together ("JQDoe").
_MAX_FUSED_INITIALS = 3
# #1431 (NDMRSO ZEIGYO 1): a running title set as cells, "<institution> | <CV
# title> | <owner>". The cells as the reader joins them (tab, " | ") and as
# `_clean_inline_tabs` rewrites them (": " for the first tab, " — " after it).
_RUNNING_TITLE_CELL_SPLIT_RE = re.compile(r"\t+|\s*\|\s*|\s+[—–]\s+|:\s+")


def _names_owner(word: str, owner_tokens: OwnerTokens) -> bool:
    """*word* is one of the owner's name words, alone or behind up to three of
    the owner's initials ("jqdoe" for an owner Jane Q. Doe)."""
    for name in owner_tokens.names:
        prefix = word[:-len(name)]
        if (word.endswith(name) and len(prefix) <= _MAX_FUSED_INITIALS
                and all(ch in owner_tokens.initials for ch in prefix)):
            return True
    return False


def _is_fused_initials(word: str, owner_tokens: OwnerTokens) -> bool:
    """Two or three of the owner's initials run together ("jqd")."""
    return (2 <= len(word) <= _MAX_FUSED_INITIALS
            and all(ch in owner_tokens.initials for ch in word))


def _is_continued_header(text: str) -> bool:
    """A section heading repeated with "(Continued)" (#1221): up to six words,
    each capitalised or a connector, with no sentence punctuation."""
    match = _CONTINUED_HEADER_RE.match(text)
    if not match:
        return False
    label = match.group("label")
    words = label.split()
    return (0 < len(words) <= _CONTINUED_LABEL_MAX_WORDS
            and not any(ch in label for ch in _SENTENCE_PUNCTUATION)
            and _is_heading_words(words))


def _fold_accents(text: str) -> str:
    """*text* without its combining accents ("résumé" -> "resume")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_running_title(text: str, owner_tokens: OwnerTokens) -> bool:
    """A running title set as cells (#1431): one cell the CV's title
    (`_CV_TITLE_RE`), at least one other a furniture line (the owner's name, a
    page), and at most one cell left over, digit-free (the institution).
    "Example University: Standardized Curriculum Vitae — Jane Q. Doe, MD"."""
    cells = [cell.strip() for cell in _RUNNING_TITLE_CELL_SPLIT_RE.split(text) if cell.strip()]
    titles = [cell for cell in cells if _CV_TITLE_RE.match(cell)]
    others = [cell for cell in cells if not _CV_TITLE_RE.match(cell)]
    leftover = [cell for cell in others if not _is_furniture_words(cell, owner_tokens)]
    return (len(titles) == 1 and len(leftover) < len(others) and len(leftover) <= 1
            and not any(ch.isdigit() for cell in leftover for ch in cell))


def is_page_furniture(text: str, owner_tokens: OwnerTokens = OwnerTokens()) -> bool:
    """True when *text* is page furniture (#1221): a "(Continued)" header, a
    running title of cells (`_is_running_title`, #1431), or a line of
    furniture words (`_is_furniture_words`)."""
    stripped = text.strip()
    return (_is_continued_header(stripped) or _is_running_title(stripped, owner_tokens)
            or _is_furniture_words(stripped, owner_tokens))


def _is_furniture_words(stripped: str, owner_tokens: OwnerTokens) -> bool:
    """A line that, once Word field-code instructions, page numbers, dates, the
    owner's name tokens and `_FURNITURE_FILLER_WORDS` are removed, holds nothing
    else, and that held a field code, a `_FURNITURE_EVIDENCE_WORDS` word or the
    owner's name (#1221). A bare date range or "May 1981" has no evidence and is
    kept; a line with any other word in it ("Example Lab, Page 2"), a citation's
    volume and pages, a phone number or a number of five digits or more is kept."""
    has_field = bool(_FIELD_SWITCH_RE.search(stripped) or _SWITCHLESS_FIELD_NAME_RE.search(stripped))
    if has_field:
        stripped = _FIELD_NAME_RE.sub(" ", _FIELD_SWITCH_RE.sub(" ", stripped))
    if _RECORD_NUMBER_RE.search(stripped):
        return False
    body = _POSSESSIVE_RE.sub("", _SPACED_PAGE_RE.sub(" page ", _fold_accents(stripped)).lower().replace(".", ""))
    evidence = has_field
    for word in _WORD_RE.findall(body):
        if word in _FURNITURE_EVIDENCE_WORDS or _names_owner(word, owner_tokens):
            evidence = True
        elif not (word in _FURNITURE_FILLER_WORDS or word in owner_tokens.removable
                  or _PAGE_NUMBER_RE.fullmatch(word) or _is_fused_initials(word, owner_tokens)):
            return False
    return evidence


def _furniture_reason(text: str, owner_tokens: OwnerTokens) -> str | None:
    """`DROP_PAGE_FURNITURE` or `DROP_ON_REQUEST` for a T entry's text, else
    None (#1221)."""
    if is_page_furniture(text, owner_tokens):
        return DROP_PAGE_FURNITURE
    if _ON_REQUEST_RE.match(text.strip().rstrip("|").strip()):
        return DROP_ON_REQUEST
    return None


# A recovered A line that needs no reviewer's attention (#1221): a school or
# affiliation line that names no rank or role ...
_AFFILIATION_LINE_RE = re.compile(
    r"^(?:current\s+|primary\s+)?(?:school|affiliation|institution|university|college|department)"
    r"\s*:\s*[^\d:]+$",
    re.IGNORECASE,
)
_RANK_WORD_RE = re.compile(
    r"\b(?:professor|instructor|lecturer|fellow|resident|chair|chief|director|dean|head"
    r"|scientist|investigator|attending|physician|adjunct|emeritus|president|officer)\b",
    re.IGNORECASE,
)
# ... a profile URL, with or without its label ...
_PROFILE_URL_RE = re.compile(r"^(?:[A-Za-z ]{1,20}:\s*)?(?:https?://|www\.)\S+$", re.IGNORECASE)
# ... or a label left bare once the PII pass withheld its value ("Contact
# Details:"); the withheld-data notice beside it says what was removed.
_BARE_LABEL_RE = re.compile(r"^[^\d:]{1,40}:$")


# #1431 (X6 UXBHHF 47): a running footer's "Email: <address>". Personal Data
# renders the address in its own cell under its own label, so the whole line is
# never found on the page even when the address is.
_LABELLED_EMAIL_RE = re.compile(rf"^(?:[A-Za-z ]{{1,20}}:\s*)?(?P<email>{_BARE_EMAIL_SHAPE})\.?$")


def labelled_email(text: str) -> str | None:
    """The address when *text* is an email address alone, or behind one short
    label ("Email: jdoe@example.edu"), else None (#1431)."""
    match = _LABELLED_EMAIL_RE.match(text.strip())
    return match.group("email") if match else None


def is_routine_recovered_line(text: str, owner_tokens: OwnerTokens = OwnerTokens()) -> bool:
    """True when a recovered A line holds nothing a reviewer must act on
    (#1221): page furniture (the owner's name, the CV title, a running header),
    a school or affiliation line naming no rank, a profile URL, or a bare
    label. A rank, a family line or a phone number is not routine."""
    stripped = text.strip()
    if _AFFILIATION_LINE_RE.match(stripped):
        return not _RANK_WORD_RE.search(stripped)
    return bool(is_page_furniture(stripped, owner_tokens)
                or _PROFILE_URL_RE.match(stripped) or _BARE_LABEL_RE.match(stripped))


def _is_bare_section_label(text: str) -> bool:
    """A one-line section label with nothing after its closing colon
    ("Sample Background:", "1. Honors or Awards:"), on its text alone
    (#530, RCBKFG UYFRTL 17). The two-signal rule dropped the same label
    only when 3b's reasoning used one of its section-header wordings, so a
    run that said "Header label only" kept it. The closing colon is what
    makes the shape safe without the reasoning: a T hobby or a skill
    ("Cooking", "Professional Experience") has none, and a label with its
    content after the colon ("Languages: Spanish") is not bare. The label
    must sit on one line, open with a capital, hold one colon (a cell break
    renders as another) and no period but the one in "etc." (a "Dr." or
    "Prof." line names a person), and pass `_is_section_label_text`
    (digit-free, no "none"). `_drop_lone_bare_labels` drops it only when it
    is alone in its Appendix group."""
    stripped = text.strip()
    if "\n" in stripped:
        return False
    body = _OUTLINE_MARKER_RE.sub("", stripped)
    return (bool(_BARE_LABEL_RE.match(body)) and body[0].isupper()
            and "." not in body.replace(_ETC, "")
            and _is_section_label_text(stripped))


class _StructuralKind(NamedTuple):
    """One two-signal structural kind: the `DROP_*` reason it reports, how 3b's
    reasoning names it, and the positive shape the entry's text must have."""

    reason: str
    reasoning_re: re.Pattern[str]
    shape: Callable[[str], bool]


# A month and year alone ("April 2020"): a CV's own date only under
# `_CV_DATE_REASONING_RE` (#1431).
_LONE_MONTH_YEAR_RE = re.compile(rf"^{_MONTH}\.?,?\s*(?:19|20)\d{{2}}$", re.IGNORECASE)

# Checked in order; the first kind whose reasoning and shape both match wins.
_STRUCTURAL_KINDS = (
    _StructuralKind(DROP_CV_TITLE, _CV_TITLE_REASONING_RE, lambda t: bool(_CV_TITLE_RE.match(t))),
    _StructuralKind(DROP_DATE_STAMP, _DATE_STAMP_REASONING_RE, lambda t: bool(_DATE_STAMP_RE.match(t))),
    _StructuralKind(DROP_COLUMN_HEADER, _COLUMN_HEADER_REASONING_RE, _is_label_only_row),
    _StructuralKind(DROP_SECTION_HEADER, _SECTION_HEADER_REASONING_RE, _is_section_label_text),
    _StructuralKind(DROP_TEMPLATE_INSTRUCTION, _TEMPLATE_INSTRUCTION_REASONING_RE, _is_template_directive),
    _StructuralKind(DROP_DATE_STAMP, _CV_DATE_REASONING_RE,
                    lambda t: bool(_DATE_STAMP_RE.match(t) or _LONE_MONTH_YEAR_RE.match(t))),
    _StructuralKind(DROP_PAGE_FURNITURE, _CONTINUATION_REASONING_RE, _is_continued_header),
)


def _confirmed_structural_reason(
    text: str,
    reasoning: str | None,
    owner_tokens: OwnerTokens = OwnerTokens(),
    prefix: str = _T_CONFIRMED_PREFIX,
) -> str | None:
    """The `DROP_*` reason for an entry that BOTH stage 3b's T-validation
    called structural of a named kind AND whose raw text has that kind's
    positive shape (#885 residual), else None. *prefix* is the T-validation
    verdict the reasoning must open with: `_T_CONFIRMED_PREFIX` for a T entry,
    `_T_RECLASSIFIED_PREFIX` for one 3b moved out of T (#1431)."""
    if not reasoning or not reasoning.startswith(prefix):
        return None
    lead = reasoning[len(prefix):].lstrip()[:_REASONING_LEAD_CHARS].lower()
    stripped = text.strip()
    for kind in _STRUCTURAL_KINDS:
        if kind.reasoning_re.search(lead) and kind.shape(stripped):
            return kind.reason
    if _SIGNATURE_REASONING_RE.search(lead) and _is_signature_block(text, owner_tokens):
        return DROP_SIGNATURE_BLOCK
    return None


class UnmappedEntry(TypedDict, total=False):
    """One pipeline entry as the appendix reads it (review on #736) -- the
    contract behind the old `List[Dict]` signature. `text` and `hierarchy` are
    what this module itself reads; the five named fields are not the whole
    entry, only what this module needs -- `_add_entry_comments`
    (stage_6_word_template.py:2407-2559) reads several more of an entry's
    fields for the per-line Word comment, among them `taxonomy_confidence`,
    `confidence`, `is_fragment`, `fragment_reasoning`, `fragment_of` and
    `t_validation_applied`. `total=False`: the stage-6 input JSON has no
    schema enforcing any key exists, so this documents the shape rather than
    validating it -- the read sites coerce a missing or None value to the
    empty case, as the rest of stage 6 does."""

    text: str
    hierarchy: list[str]
    taxonomy_code: str
    extracted_fields: dict[str, object]
    classification_reasoning: str


# One appendix line: the entry (kept for its per-entry comments) and its body,
# with the readers' cell separators already collapsed.
AppendixLine = tuple[UnmappedEntry, str]


# Which pass put a recovered line's `code` on it (#1225). ENTRY: the entry's
# own code as stage 6 received it (stage 3b's, or stage 4's quarantine T).
# RECONSIDER: `_reconsider_appendix_entries` split an overflow entry into
# segments and coded each one itself, so the code is not a stage 3b
# classification and the warning must not read as one.
CODE_ORIGIN_ENTRY = "entry"
CODE_ORIGIN_RECONSIDER = "reconsider"


class RecoveredLine(NamedTuple):
    """One bullet `_add_remaining_to_appendix` wrote into the Appendix: the
    taxonomy code that classified it, the text the reader sees, and which
    pass assigned the code (`CODE_ORIGIN_*`). The text decides the A
    warning's severity (#1221); the origin decides its wording (#1225)."""

    code: str
    text: str
    origin: str = CODE_ORIGIN_ENTRY


# Why a taxonomy code's entries were diverted to the Appendix (#531,
# #531-R2). Named constants rather than inline strings because they are a
# comparison target for `_appendix_diversion_reason` and, once emitted, a
# re-emission key for the doctor -- a typo in one spelling and the other
# would silently create a third, undocumented reason.
REASON_NO_RENDER_ROUTE = "no_render_route"
REASON_RENDERER_DECLINED = "renderer_declined"

# The third REASON_RENDERER_DECLINED mechanism (#839): an M2A/M2B/M2C entry
# `_create_grant_table` declined as too sparse to table. Named here, not
# imported from `research_support.py` (a `stage6/sections/*` peer -- see the
# module-docstring comment above `_REASON_TEXT` for why peers pass constants
# down instead of importing each other), so `_diversion_message` can tell it
# apart from the M1 no-research-summary case, which the shared `_REASON_TEXT`
# string actually describes.
_DECLINED_GRANT_CODES = frozenset({'M2A', 'M2B', 'M2C'})
# A record `_add_remaining_to_appendix` bulleted on behalf of
# `_reconsider_appendix_entries` / `_recover_unrendered_records` (#531-R2
# finding F1) -- distinct from the two reasons above because it is not
# about routing at all: the code MAY be fully routed (`RENDER_ROUTED_CODES`
# member), the structured render for it simply did not find this specific
# record. Applies regardless of the code's own routing status.
REASON_RECOVERED_UNRENDERED = "recovered_unrendered"

# The severities an `appendix_diversion` warning carries into the sidecar,
# which `lint_stage6_warnings` re-emits as is (#1221).
SEVERITY_INFO = "INFO"
SEVERITY_WARN = "WARN"
# The Personal Data code: its recovered lines are mostly the owner's own banner,
# so only its warning is split by what the lines hold.
_PERSONAL_DATA_CODE = "A"

# Stage 4 (`stage4/code_check.py`, #651) re-coded this entry to T because its
# stage 3b code is not in taxonomy_v7.json. Distinct from REASON_NO_RENDER_ROUTE
# (a VALID code nothing renders): here the code was never a real one. Keyed off
# the entry's `taxonomy_code_quarantine_reason` marker, not the code -- a
# quarantined entry's code is the ordinary T. The string is duplicated from
# `code_check.INVALID_CODE_REASON` rather than imported (stage6 does not import
# stage 4; the stage 6 input JSON is the contract).
REASON_INVALID_CODE = "invalid_code"
_QUARANTINE_MARKER_KEY = "taxonomy_code_quarantine_reason"
_QUARANTINE_MARKER_INVALID_CODE = "invalid_taxonomy_code"

# Stage 3b's T-validation pass recoded this entry from T to M1. The research
# summary only paraphrases M1, so such an entry (a website, a project note) is
# diverted here rather than lost (AUTOPSY-s7ab-batch-2026-10-02 class 11:
# RGUNJV 3038/3040, BMAMWE 1282..). Keyed off stage 3b's `t_validation_applied`
# marker, which on a non-T entry means T-validation moved it off T
# (stage3b/classify.py `validate_t_classifications`); the string is duplicated
# rather than imported, as with the quarantine marker above.
REASON_T_VALIDATION_RECODED = "t_validation_recoded"
_T_VALIDATION_MARKER_KEY = "t_validation_applied"

# A dated M1 record (a position, a project) that a rendered, generated research
# summary does not reproduce and no other section renders, so `generate()` sends
# it here (AUTOPSY-EBYSBC-batch-2026-10-02 class E27: CMTQDR 122, XWNZWW 184).
# No other route brings an M1 entry to the Appendix while the summary rendered,
# which is how `build_appendix_diversion_warnings` tells this reason from
# REASON_RENDERER_DECLINED.
REASON_M1_RECORD_NOT_IN_SUMMARY = "m1_record_not_in_summary"

# E, G and J -- the three passthrough sections. None of the three is in
# `RENDER_ROUTED_CODES` (they have no taxonomy-code dispatch of their own --
# the passthrough writers select by source hierarchy, not code), so an
# unconsumed E/G/J entry would otherwise read as REASON_NO_RENDER_ROUTE,
# which is false: `_fill_passthrough_sections` IS their renderer, and it
# declined this specific entry (label or table shape did not match) rather
# than there being no route at all (#531-R2 finding F2).
#
# `PASSTHROUGH_CODES` is `stage6/sections/passthrough.py`'s own constant
# (#531-R3 task 4 / finding F-R2-4) -- but `appendix.py` does NOT import it:
# both modules are `stage6/sections/*` peers, and CODING_STANDARDS.md 1.3
# ("Peers do not import peers" -- [gate]) forbids exactly that edge (a real
# regression this round hit: `check_standards.py` flagged "1.3 peers do not
# import peers 0 -> 1" the first time this was written as a direct import;
# reverted to this parameter-threading approach, the same one
# `render_routed_codes` below already uses for the same reason --
# `RENDER_ROUTED_CODES` lives in `stage_6_word_template.py`, which is not a
# `sections/*` peer and is free to import both `appendix.py` and
# `passthrough.py` and pass each module's constant down as an argument).
# No existing constant elsewhere is named for that triple's role in this
# module (`doctor/lints/extraction.py`'s
# `_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES` happens to hold the same three
# codes since #587, but it is a DIFFERENT set, owned by a different lint).

# The one code whose REASON_RENDERER_DECLINED case is the M1 conditional
# discard in `generate()` -- every OTHER routed code that reaches
# REASON_RENDERER_DECLINED got there because its own section's renderer
# raised (#842), which is a different mechanism and needs a different
# message; see `_diversion_message`.
_RESEARCH_SUMMARY_CODE = 'M1'

# Human-readable text for the two routing-based reasons, used only inside
# `message` -- the `reason` field itself stays the stable machine key above.
# REASON_RENDERER_DECLINED now covers three mechanisms with three different
# messages (the M1 no-research-summary discard, the E/G/J passthrough
# refusal, and #842's failed-section discard) so it is NOT looked up here;
# see `_diversion_message`.
# REASON_RECOVERED_UNRENDERED's message is also code-specific (repeats the
# code) so it is built directly in `_diversion_message` too.
_REASON_TEXT = {
    REASON_NO_RENDER_ROUTE: "no stage 6 section is routed to render this taxonomy code",
    REASON_RENDERER_DECLINED: "no research summary rendered",
}


def is_t_validation_recoded_m1(entry: Mapping[str, object]) -> bool:
    """True when stage 3b's T-validation pass moved *entry* from T to M1 --
    content the research summary does not render (REASON_T_VALIDATION_RECODED)."""
    return (entry.get("taxonomy_code") == _RESEARCH_SUMMARY_CODE
            and entry.get(_T_VALIDATION_MARKER_KEY) is True)


def _plural_entries(count: int) -> str:
    """'entry' for 1, 'entries' otherwise -- the noun in `_diversion_message`."""
    return "entry" if count == 1 else "entries"


def _plural_was(count: int) -> str:
    """'was' for 1, 'were' otherwise -- REASON_RECOVERED_UNRENDERED's verb,
    the one message shape whose grammar needs subject-verb agreement."""
    return "was" if count == 1 else "were"


def _diversion_message(code: str, count: int, reason: str,
                        passthrough_codes: frozenset[str],
                        origin: str = CODE_ORIGIN_ENTRY) -> str:
    """The Appendix-diversion warning's human-readable `message` (#531,
    #531-R2, #839, #842). Five shapes, by *reason*:

    - REASON_RECOVERED_UNRENDERED: always names *code* twice (the code that
      classified the record, spelled out rather than left implicit, since
      this reason has nothing to do with routing). *origin* says who coded
      it: CODE_ORIGIN_RECONSIDER names the stage 6 reconsider pass and calls
      the lines segments, so a code that pass gave a split-off segment does
      not read as a stage 3b classification of an entry (#1225).
    - REASON_RENDERER_DECLINED for a passthrough code (E/G/J,
      *passthrough_codes* -- `stage6/sections/passthrough.py`'s
      `PASSTHROUGH_CODES`, passed in rather than imported; see the module
      docstring comment above `_REASON_TEXT` for why): names the passthrough
      writer specifically -- the generic `_REASON_TEXT` string is for the
      OTHER REASON_RENDERER_DECLINED case (M1) and would misdescribe this
      one. Phrased passive ("refused by...") rather than "...declined
      them": the pronoun read wrong in the singular ("1 entry ... declined
      them") (#531-R3 finding r11).
    - REASON_RENDERER_DECLINED for an M2A/M2B/M2C code (`_DECLINED_GRANT_CODES`,
      #839): names the research-support renderer's own decline (too sparse
      to table) -- checked BEFORE the generic branch below, which would
      otherwise claim these too.
    - REASON_RENDERER_DECLINED for any other routed code (#842's
      failed-section discard -- checked AFTER the passthrough branch above,
      so it never fires for E/G/J, and after excluding
      `_RESEARCH_SUMMARY_CODE` so M1's own case still gets the
      `_REASON_TEXT` message below): names the mechanism generically --
      unlike the passthrough case there is no single writer to name, since
      any of the ~20 routed sections could be the one whose renderer raised
      -- and points at the section-failure record rather than repeating the
      section name (`_diversion_message` has no `label`, only `code`).
    - REASON_T_VALIDATION_RECODED (and REASON_INVALID_CODE): stage 3b's
      recode named as the cause, checked before any routing-based reason.
    - REASON_M1_RECORD_NOT_IN_SUMMARY: a dated record the generated research
      summary left out (E27), likewise checked before the routing-based ones.
    - Everything else (REASON_NO_RENDER_ROUTE, and REASON_RENDERER_DECLINED
      for `_RESEARCH_SUMMARY_CODE`): the shared `_REASON_TEXT` lookup.
    """
    noun = _plural_entries(count)
    if reason == REASON_INVALID_CODE:
        return (f"{code}: {count} {noun} diverted to the Appendix — stage 4 "
                f"quarantined {'it' if count == 1 else 'them'}: the stage 3b "
                f"taxonomy code was not a valid taxonomy code (see "
                f"original_taxonomy_code in the stage 4 artifact)")
    if reason == REASON_T_VALIDATION_RECODED:
        return (f"{code}: {count} {noun} diverted to the Appendix — stage 3b "
                f"T-validation recoded {'it' if count == 1 else 'them'} from T "
                f"to {code}, which only the research summary renders")
    if reason == REASON_M1_RECORD_NOT_IN_SUMMARY:
        pronoun = 'it' if count == 1 else 'them'
        return (f"{code}: {count} dated {noun} diverted to the Appendix — the "
                f"generated research summary does not reproduce {pronoun} and no "
                f"other section renders {pronoun}")
    if reason == REASON_RECOVERED_UNRENDERED and origin == CODE_ORIGIN_RECONSIDER:
        verb = _plural_was(count)
        segments = 'segment' if count == 1 else 'segments'
        return (f"{code}: {count} {segments} of overflow content that the "
                f"stage 6 reconsider pass coded {code} {verb} not placed in a "
                f"section and {verb} recovered into the Appendix")
    if reason == REASON_RECOVERED_UNRENDERED:
        verb = _plural_was(count)
        return (f"{code}: {count} {noun} classified {code} {verb} not "
                f"found in the rendered document and {verb} recovered into "
                f"the Appendix")
    if reason == REASON_RENDERER_DECLINED and code in passthrough_codes:
        return (f"{code}: {count} {noun} diverted to the Appendix — "
                f"refused by the passthrough writer for {code} (source "
                f"section label did not match)")
    if reason == REASON_RENDERER_DECLINED and code in _DECLINED_GRANT_CODES:
        return (f"{code}: {count} {noun} diverted to the Appendix — "
                f"declined by the research-support renderer as too sparse "
                f"to table")
    if reason == REASON_RENDERER_DECLINED and code != _RESEARCH_SUMMARY_CODE:
        return (f"{code}: {count} {noun} diverted to the Appendix — "
                f"not placed by the section routed for {code} (see any "
                f"section_render_failed record for that section)")
    return (f"{code}: {count} {noun} diverted to the Appendix — "
            f"{_REASON_TEXT[reason]}")


class AppendixDiversionWarning(TypedDict):
    """One `_validate_output`-shaped warning naming a taxonomy code's
    Appendix diversion (#531) -- same `check`/`section`/`message`/`evidence`
    keys the three existing sidecar checks use
    (`stage_6_word_template.py:_validate_output`), plus the two structured
    keys `lint_stage6_warnings` cannot recover from `message` alone: `code`
    and `count`. `evidence` is always `[]` by design -- this check reports a
    count, never entry text (PII surface; the Appendix itself already
    carries the text). `severity` is what `lint_stage6_warnings` re-emits:
    INFO only for recovered A lines that are all routine (#1221), WARN
    otherwise."""

    check: str
    code: str
    section: str
    count: int
    reason: str
    message: str
    evidence: list[str]
    severity: str


def _appendix_diversion_reason(code: str, render_routed_codes: frozenset[str],
                                passthrough_codes: frozenset[str]) -> str:
    """Which of the two ROUTING-based ways *code*'s entries ended up
    diverted to the Appendix as NUMBERED lines (`_fill_appendix`'s own
    output -- the third reason, REASON_RECOVERED_UNRENDERED, is not routing
    -based and is assigned directly by its caller, never through this
    function). *render_routed_codes* is `RENDER_ROUTED_CODES`
    (`stage_6_word_template.py`) -- the AUTHORITATIVE routed-code set, not
    the per-call `mapped_codes` copy `generate()` mutates (the M1 discard).
    *passthrough_codes* is `stage6/sections/passthrough.py`'s
    `PASSTHROUGH_CODES`, likewise passed in rather than imported (see the
    module docstring comment near `_REASON_TEXT`). A passthrough code is
    always REASON_RENDERER_DECLINED -- checked FIRST, since E/G/J are never
    in `render_routed_codes` and would otherwise fall into the next branch
    (#531-R2 finding F2). Otherwise: a code absent from `render_routed_codes`
    never had a renderer at all (`REASON_NO_RENDER_ROUTE`); a code present
    in it still reached the Appendix only because this run's `mapped_codes`
    copy discarded it (`REASON_RENDERER_DECLINED`, the M1 case) -- passed
    the frozenset rather than the discard reason itself because today there
    is exactly one such discard case and the two-way split is all
    `generate()` needs to convey.
    """
    if code in passthrough_codes:
        return REASON_RENDERER_DECLINED
    if code not in render_routed_codes:
        return REASON_NO_RENDER_ROUTE
    return REASON_RENDERER_DECLINED


def build_appendix_diversion_warnings(
    written: Sequence[UnmappedEntry],
    recovered: Sequence[RecoveredLine],
    render_routed_codes: frozenset[str],
    passthrough_codes: frozenset[str],
    owner_tokens: OwnerTokens = OwnerTokens(),
    *,
    summary_rendered: bool = False,
) -> list[AppendixDiversionWarning]:
    """One `appendix_diversion` warning per (taxonomy code, reason, code
    origin) actually present in the Appendix (#531, #531-R2 finding F1,
    #1225). Two input streams, both post-filter (nothing dropped survives
    either):

    - *written*: the entries `_fill_appendix` put on the page as NUMBERED
      lines, i.e. AFTER `_appendix_drop_reason` filtering. Reason is
      `_appendix_diversion_reason`'s routing-based split.
    - *recovered*: one `RecoveredLine` per "bullet" line
      `_add_remaining_to_appendix` wrote on behalf of
      `_reconsider_appendix_entries` / `_recover_unrendered_records` --
      always REASON_RECOVERED_UNRENDERED, regardless of the code's own
      routing status, since these exist because a specific record did not
      render, not because its code lacks a route. A line's `origin` splits
      its code's warning in two when the stage 6 reconsider pass coded some
      of the lines and the entry's own code covers the rest: one message
      cannot say who classified both (#1225). Numbered lines are always
      CODE_ORIGIN_ENTRY.

    Every warning is WARN except one: the recovered A warning is INFO when
    every recovered A line is routine (`is_routine_recovered_line`, which
    reads *owner_tokens* for the owner's name) -- a name banner, a school
    line or a profile URL says nothing went wrong (#1221). Numbered T lines
    need no such split: page furniture is dropped before they are counted.

    *passthrough_codes* (`stage6/sections/passthrough.py`'s
    `PASSTHROUGH_CODES`, #531-R3 task 4) is threaded through to
    `_appendix_diversion_reason` and `_diversion_message` rather than
    imported here -- `appendix.py` and `passthrough.py` are both
    `stage6/sections/*` peers, and CODING_STANDARDS.md 1.3 ("Peers do not
    import peers", `[gate]`) forbids that import edge; the caller
    (`generate()` in `stage_6_word_template.py`, which is not a `sections/*`
    peer) imports both modules' constants and passes them down, the same
    way it already does for `render_routed_codes`.

    *summary_rendered* is whether `generate()` placed the research summary.
    When it did, M1 stays routed, so an M1 entry in *written* that stage 3b's
    T-validation did not recode is a dated record the summary left out
    (REASON_M1_RECORD_NOT_IN_SUMMARY); when it did not, every M1 entry is
    there because no summary rendered (REASON_RENDERER_DECLINED).

    Sorted by (code, reason, origin) so the sidecar is deterministic and,
    when one code has entries in both streams (e.g. some T lines numbered,
    others bulleted), its warnings are adjacent. An entry/code with no
    `taxonomy_code` groups under `"?"` rather than being silently skipped --
    the total count must still reconcile against the docx line total
    (EXPECTED OUTCOME 5 / F1 self-consistency).
    """
    counts: Counter[tuple[str, str, str]] = Counter()
    for entry in written:
        code = entry.get("taxonomy_code") or "?"
        if entry.get(_QUARANTINE_MARKER_KEY) == _QUARANTINE_MARKER_INVALID_CODE:
            reason = REASON_INVALID_CODE
        elif is_t_validation_recoded_m1(entry):
            reason = REASON_T_VALIDATION_RECODED
        elif code == _RESEARCH_SUMMARY_CODE and summary_rendered:
            reason = REASON_M1_RECORD_NOT_IN_SUMMARY
        else:
            reason = _appendix_diversion_reason(code, render_routed_codes, passthrough_codes)
        counts[(code, reason, CODE_ORIGIN_ENTRY)] += 1
    routine: Counter[tuple[str, str]] = Counter()
    for line in recovered:
        code = line.code or "?"
        counts[(code, REASON_RECOVERED_UNRENDERED, line.origin)] += 1
        if code == _PERSONAL_DATA_CODE and is_routine_recovered_line(line.text, owner_tokens):
            routine[(code, line.origin)] += 1

    warnings: list[AppendixDiversionWarning] = []
    for code, reason, origin in sorted(counts):
        count = counts[(code, reason, origin)]
        all_routine = reason == REASON_RECOVERED_UNRENDERED and routine[(code, origin)] == count
        warnings.append({
            "check": "appendix_diversion",
            "code": code,
            "section": "T. APPENDIX",
            "count": count,
            "reason": reason,
            "message": _diversion_message(code, count, reason, passthrough_codes, origin),
            "evidence": [],
            "severity": SEVERITY_INFO if all_routine else SEVERITY_WARN,
        })
    return warnings


def _appendix_drop_reason(
    text: str,
    rendered: str,
    taxonomy_code: str | None = None,
    reasoning: str | None = None,
    owner_tokens: OwnerTokens = OwnerTokens(),
) -> str | None:
    """The `DROP_*` reason *text* stays out of the appendix, or None to keep it.

    *rendered* is *text* with the readers' cell separators collapsed; it is
    passed in rather than recomputed so each survivor is rendered once.

    *taxonomy_code* gates the four structural checks (bare year, status
    marker, ToC line, rule line -- see the module docstring and the comments above
    `_BARE_YEAR_RE`) to T-coded entries only. Optional, defaulting to None
    (which skips those four checks), so a caller checking only text shape --
    `test_stage6_appendix_header_row_filter.py`'s unit tests among them --
    is unaffected.

    *reasoning* (the entry's `classification_reasoning`) feeds the #885
    residual check, `_confirmed_structural_reason`, which is likewise T-only
    and additionally needs the text's own positive shape. *owner_tokens* (the
    owner name stage 6 holds, see `_owner_signature_tokens`) lets the signature-block
    shape recognise "Signed: <owner>" (#1221); empty skips that recognition.
    A T entry nothing above caught last meets `_furniture_reason` (#1221):
    page furniture, which also reads *owner_tokens* for a running header, and
    "References available on request".
    """
    if not text.strip():
        return DROP_BLANK
    if is_template_instruction(text) or is_foreign_template_instruction(text):
        return DROP_TEMPLATE_INSTRUCTION
    if is_source_boilerplate(text):
        return DROP_SOURCE_BOILERPLATE
    if not rendered.strip():
        return DROP_RENDERS_EMPTY
    if _is_column_header_row(text):
        return DROP_COLUMN_HEADER
    if taxonomy_code == _APPENDIX_TAXONOMY_CODE:
        stripped = text.strip()
        if _BARE_YEAR_RE.match(stripped):
            return DROP_BARE_YEAR
        if stripped.rstrip(".").lower() in _STATUS_MARKER_WORDS:
            return DROP_STATUS_MARKER
        if _TOC_LINE_RE.match(text):
            return DROP_TOC_LINE
        if _RULE_LINE_RE.match(stripped):
            return DROP_RULE_LINE
    if is_near_template_instruction(text):
        return DROP_NEAR_TEMPLATE_INSTRUCTION
    if is_unanswered_prompt(text):
        return DROP_UNANSWERED_PROMPT
    if is_template_label_line(text):
        return DROP_TEMPLATE_LABEL
    # Last, so every older check keeps the drop it already claimed; only
    # entries nothing else caught reach the two-signal residual test.
    if taxonomy_code == _APPENDIX_TAXONOMY_CODE:
        return (_confirmed_structural_reason(text, reasoning, owner_tokens)
                or _furniture_reason(text, owner_tokens))
    return reclassified_structural_reason(text, reasoning, owner_tokens)


def reclassified_structural_reason(
    text: str,
    reasoning: str | None,
    owner_tokens: OwnerTokens = _NO_OWNER_TOKENS,
) -> str | None:
    """The `DROP_*` reason for an entry 3b's T-validation moved out of T while
    naming it a structural kind whose shape its text has (#1431), else None.
    The Appendix filter reads it for every non-T entry; stage 6's personal-data
    recovery reads it for an A orphan."""
    return _confirmed_structural_reason(text, reasoning, owner_tokens, _T_RECLASSIFIED_PREFIX)


def _filter_unmapped_entries(
    entries: Sequence[UnmappedEntry],
    owner_tokens: OwnerTokens = OwnerTokens(),
) -> tuple[list[AppendixLine], Counter[str]]:
    """Split *entries* into the lines the appendix will show and a count of
    drops per `DROP_*` reason."""
    kept: list[AppendixLine] = []
    dropped: Counter[str] = Counter()
    for entry in entries:
        text = entry.get("text") or ""
        rendered = _clean_inline_tabs(text)
        reason = _appendix_drop_reason(
            text,
            rendered,
            entry.get("taxonomy_code"),
            entry.get("classification_reasoning"),
            owner_tokens,
        )
        if reason is None:
            kept.append((entry, rendered))
        else:
            dropped[reason] += 1
    return _drop_lone_bare_labels(kept, dropped), dropped


def _drop_lone_bare_labels(
    kept: Sequence[AppendixLine],
    dropped: Counter[str],
) -> list[AppendixLine]:
    """*kept* without each T line that is a bare label
    (`_is_bare_section_label`) and the only line of its source-heading group,
    each counted on *dropped* as `section-header` (#530, RCBKFG UYFRTL 17).
    A label with other lines of its group beside it is a lead-in to them
    ("Sample reviewer for:" over the venues it introduces) and is kept."""
    group_sizes = Counter(_source_heading(entry) for entry, _ in kept)
    result: list[AppendixLine] = []
    for entry, rendered in kept:
        if (entry.get("taxonomy_code") == _APPENDIX_TAXONOMY_CODE
                and group_sizes[_source_heading(entry)] == 1
                and _is_bare_section_label(rendered)):
            dropped[DROP_SECTION_HEADER] += 1
        else:
            result.append((entry, rendered))
    return result


def _source_heading(entry: UnmappedEntry) -> str:
    """The top-level source heading an Appendix line is grouped under:
    `hierarchy[0]`, or `_UNKNOWN_SECTION` for an entry with no hierarchy."""
    hierarchy = entry.get("hierarchy") or []
    return hierarchy[0] if hierarchy else _UNKNOWN_SECTION


def _group_by_source_heading(
    lines: Sequence[AppendixLine],
) -> dict[str, list[AppendixLine]]:
    """Group *lines* under their top-level source heading, in first-seen order.

    The key is `hierarchy[0]`, the heading text the reader sees in the
    `From "...":` label -- see the module docstring for why that is the
    intended key and the corpus measurement behind it. An entry with no
    hierarchy files under `_UNKNOWN_SECTION`.
    """
    groups: dict[str, list[AppendixLine]] = {}
    for entry, text in lines:
        groups.setdefault(_source_heading(entry), []).append((entry, text))
    return groups


def _truncate_appendix_text(text: str) -> str:
    """Cap *text* at `APPENDIX_MAX_CHARS`, marker included, so a rendered body
    is never longer than the documented limit."""
    if len(text) <= APPENDIX_MAX_CHARS:
        return text
    return text[: APPENDIX_MAX_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _describe_dropped(dropped: Counter[str]) -> str:
    """'3 non-content blocks removed (column-header 2, blank 1)' -- the summary
    comment's text, reasons in `DROP_REASONS` order, zero counts omitted."""
    total = sum(dropped.values())
    breakdown = ", ".join(
        f"{reason} {dropped[reason]}" for reason in DROP_REASONS if dropped[reason]
    )
    plural = "s" if total != 1 else ""
    return f"{total} non-content block{plural} removed ({breakdown})"


class AppendixSection:
    """Section T writers, mixed into `WCMTemplateGenerator`."""

    def _fill_appendix(
        self,
        unmapped_entries: Sequence[UnmappedEntry],
        cv_owner: Mapping[str, object] | None = None,
    ) -> list[UnmappedEntry]:
        """Write Section T for the entries that reached no other section.

        Filtering and grouping are the module-level functions above; this
        method and the two `_write_appendix_*` helpers only put the result on
        the page, in S. BIBLIOGRAPHY's style.

        Returns the entries actually written as numbered Appendix lines --
        *unmapped_entries* minus whatever `_appendix_drop_reason` (or an
        empty *unmapped_entries*) dropped -- the same "report back what was
        actually consumed" contract `_fill_passthrough_sections` already
        uses (#531). `_group_by_source_heading` only reorders `lines`, it
        drops nothing further, so this is exactly the numbered-line set the
        caller can count per taxonomy code.
        """
        if not unmapped_entries:
            return []

        lines, dropped = _filter_unmapped_entries(
            unmapped_entries, _owner_signature_tokens(cv_owner))
        if dropped:
            logger.info("Appendix: %s", _describe_dropped(dropped))
        if not lines:
            return []

        if self.verbose:
            logger.info("Adding Appendix (%d unmapped entries)...", len(lines))

        self._write_appendix_intro(dropped, len(lines))
        groups = _group_by_source_heading(lines)
        for position, (heading, group) in enumerate(groups.items()):
            self._write_appendix_group(heading, group, first=position == 0)

        return [entry for entry, _ in lines]

    def _write_appendix_intro(self, dropped: Counter[str], count: int) -> None:
        """The section header, its CViche note box and, when anything was
        dropped, the ONE summary comment saying what and why."""
        note = self._write_appendix_header(count)
        if dropped:
            self._add_word_comment(note, _describe_dropped(dropped), author="Template Filter")

    def _write_appendix_header(self, count: int) -> Paragraph:
        """"T. APPENDIX", then the CViche note box for *count* entries, then a
        blank line; returns the box's instruction paragraph. Both appendix
        writers open the section this way."""
        # Blank paragraph before the header, matching BIBLIOGRAPHY.
        self.doc.add_paragraph()

        appendix_para = self.doc.add_paragraph()
        run = appendix_para.add_run("T. APPENDIX")
        _set_font(run, bold=True)
        run.underline = True

        note = add_cviche_box(self.doc, APPENDIX_NOTE_TITLE).add_paragraph()
        cviche_box_line(note, appendix_note_text(count))

        # Blank paragraph after the note box.
        self.doc.add_paragraph()
        return note

    def _set_appendix_note_count(self, count: int) -> None:
        """Restate the Appendix note box's instruction for the final *count*
        of entries: numbered lines and recovered bullets both."""
        for table in self.doc.tables:
            if is_cviche_box(table) and table.cell(0, 0).paragraphs[0].text == APPENDIX_NOTE_TITLE:
                note = table.cell(0, 0).paragraphs[1]
                for run in note.runs[1:]:
                    run._r.getparent().remove(run._r)
                note.runs[0].text = appendix_note_text(count)
                return

    def _appendix_body(self, entry: UnmappedEntry, text: str) -> str:
        """The body an Appendix line shows for *entry*: its whole *text* when
        it is T-coded, otherwise capped by `_truncate_appendix_text` (#1230).

        The Appendix is a T entry's only render, so a cut there lost its tail.
        The exception is a T entry the low-coverage overflow takes
        (`is_overflow_candidate`): the reconsider pass re-splits it and routes
        its segments, and a whole copy here would repeat them.
        """
        if (entry.get("taxonomy_code") == _APPENDIX_TAXONOMY_CODE
                and not self._is_overflow_candidate(entry)):
            return text
        return _truncate_appendix_text(text)

    def _write_appendix_group(
        self, heading: str, lines: Sequence[AppendixLine], first: bool
    ) -> None:
        """One block under the source CV's own section heading, in its own
        wording and in bold, with its lines numbered from 1."""
        # Blank paragraph between groups (not before the first).
        if not first:
            self.doc.add_paragraph()

        header_para = self.doc.add_paragraph()
        run = header_para.add_run(appendix_group_heading(heading))
        _set_font(run, bold=True)

        for number, (entry, text) in enumerate(lines, start=1):
            entry_para = self.doc.add_paragraph()
            run = entry_para.add_run(f"{number}. {self._appendix_body(entry, text)}")
            _set_font(run)
            # Per-entry comments (e.g. why it was classified T).
            self._add_entry_comments(entry_para, entry)
            self.stats['entries_inserted'] += 1
