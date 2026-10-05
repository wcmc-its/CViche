"""Read sections E, G and J verbatim out of a WCM-format source CV (#1463).

A CV written in the WCM template already holds E (Employment Status), G
(Institutional/Hospital Affiliation) and J (Percent Effort) in the shape the
output wants. Rebuilding them from extracted fields into fixed template slots
loses content: E kept only 4 keyword labels, G forced 3 label rows, J 5 fixed
rows. So when the source is WCM-format, the source block is copied instead.

The 2026-10-05 census of every source on S3 (192 unique files) set the rules
here: 25 are WCM-format; of their E/G/J blocks ~30% are unfilled template text
(left alone, so the writers run as before), people type the answer into the
label cell itself (so a block is only "unfilled" when no line carries content),
blocks are 2-11 elements long, and outside WCM-format sources the
heading-to-heading capture is unreliable (median 174 elements for G), which is
why nothing here runs for them.

No `stage_6_word_template` import (a back-edge, `stage6/__init__.py`); the
heading predicates come in as `letter_of`, owned by `sections/passthrough.py`.
"""
from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Callable, Iterable
from copy import deepcopy

from docx import Document
from docx.oxml.ns import qn
from lxml.etree import _Element

from ..core.template_boilerplate import (
    is_near_template_instruction, is_template_instruction, is_template_label_line,
    is_unanswered_prompt, normalize_template_text, template_section_headers,
)

logger = logging.getLogger(__name__)

LetterOf = Callable[[str], 'str | None']

# A WCM-format source has at least this many template headings, and that many
# in template order (#1463: the same signal #829 proposes for template mode).
WCM_MIN_HEADINGS = 4
# A heading is a short paragraph; anything longer is body text that mentions one.
HEADING_MAX_LEN = 120
# A passthrough heading recognized by `letter_of` alone (not in the header list)
# must be this short and colon-free: "Current Employment Status (Please choose
# one...):" is a label line inside E, not E's heading.
LOOSE_HEADING_MAX_LEN = 80
# The census's longest E/G/J block was 11 elements; a block past this swallowed
# a later section whose heading wasn't recognized, so it is not copied.
MAX_BLOCK_ELEMENTS = 25

_LEADING_LETTER_RE = re.compile(r"^\s*(?:[A-Z]|[IVX]{1,4}|\d{1,2})[.)]\s*")
_HEADERS = frozenset(normalize_template_text(_LEADING_LETTER_RE.sub("", h))
                     for h in template_section_headers())
# The October-2022 template's section order, by a phrase each heading contains;
# older template letterings carry the same phrases, so one list serves both.
_SECTION_ORDER = (
    "PERSONAL DATA", "EDUCATION", "POSTDOCTORAL TRAINING", "PROFESSIONAL POSITIONS", "EMPLOYMENT STATUS",
    "LICENSURE", "AFFILIATION", "HONORS", "PROFESSIONAL ORGANIZATIONS", "PERCENT EFFORT",
    "EDUCATIONAL CONTRIBUTIONS", "CLINICAL PRACTICE", "RESEARCH", "MENTORING", "INSTITUTIONAL LEADERSHIP",
    "INSTITUTIONAL ADMINISTRATIVE", "EXTRAMURAL", "INVITATIONS TO SPEAK", "BIBLIOGRAPHY",
)

# Copied XML points at the SOURCE's styles, numbering, comments, footnotes and
# relationships, none of which exist in the output package. These are removed
# outright (with their content: a w:del is deleted text, a drawing an image).
_DROP = frozenset(qn(t) for t in (
    "w:del", "w:moveFrom", "w:commentRangeStart", "w:commentRangeEnd", "w:commentReference",
    "w:bookmarkStart", "w:bookmarkEnd", "w:proofErr", "w:numPr", "w:pStyle", "w:rStyle", "w:tblStyle",
    "w:rFonts", "w:sz", "w:szCs", "w:color", "w:highlight", "w:shd", "w:sectPr",
    "w:pPrChange", "w:rPrChange", "w:tblPrChange", "w:trPrChange", "w:tcPrChange",
    "w:drawing", "w:pict", "w:object", "w:footnoteReference", "w:endnoteReference",
    "w:sdtPr", "w:sdtEndPr",
))
# ... and these are wrappers whose children are kept: a w:ins is accepted text,
# a hyperlink keeps its text but loses its (source) relationship.
_UNWRAP = frozenset(qn(t) for t in (
    "w:ins", "w:moveTo", "w:hyperlink", "w:smartTag", "w:customXml", "w:sdt", "w:sdtContent",
))
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_ID_ATTRS = (f"{{{_W14}}}paraId", f"{{{_W14}}}textId")


def element_text(el: _Element) -> str:
    """Visible text of a paragraph or table: every w:t (inserted text
    included), never w:delText. Paragraphs are space-joined so a multi-line
    table cell doesn't run its lines together."""
    if el.tag == qn("w:p"):
        return "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
    return " ".join(filter(None, (element_text(p) for p in el.iter(qn("w:p")))))


def block_lines(elements: Iterable[_Element]) -> list[str]:
    """One normalized line per non-empty paragraph and per non-empty table cell."""
    lines = []
    for el in elements:
        if el.tag == qn("w:tbl"):
            texts = [element_text(tc) for tc in el.iter(qn("w:tc"))]
        else:
            texts = [element_text(el)]
        lines.extend(normalize_template_text(t) for t in texts if t)
    return lines


def is_template_heading(text: str, letter_of: LetterOf) -> bool:
    """`text` is a section heading of some WCM template version."""
    if not text or len(text) > HEADING_MAX_LEN:
        return False
    if normalize_template_text(_LEADING_LETTER_RE.sub("", text)).rstrip(":") in _HEADERS:
        return True
    return letter_of(text) is not None and ":" not in text and len(text) <= LOOSE_HEADING_MAX_LEN


def _section_rank(text: str) -> int | None:
    upper = text.upper()
    return next((i for i, phrase in enumerate(_SECTION_ORDER) if phrase in upper), None)


def _longest_increasing_run(ranks: list[int]) -> int:
    tails: list[int] = []
    for rank in ranks:
        i = bisect.bisect_left(tails, rank)
        tails[i:i + 1] = [rank]
    return len(tails)


def is_wcm_format(heading_texts: list[str]) -> bool:
    """At least WCM_MIN_HEADINGS distinct template sections, that many in order."""
    ranks = [r for r in map(_section_rank, heading_texts) if r is not None]
    return len(set(ranks)) >= WCM_MIN_HEADINGS and _longest_increasing_run(ranks) >= WCM_MIN_HEADINGS


def section_blocks(body: _Element, letter_of: LetterOf) -> tuple[list[str], dict[str, tuple[_Element, list[_Element]]]]:
    """(every heading's text, {letter: (heading paragraph, elements up to the
    next heading)}) for the first E, G and J heading in `body`."""
    children = [el for el in body.iterchildren() if el.tag in (qn("w:p"), qn("w:tbl"))]
    heads = [(i, element_text(el)) for i, el in enumerate(children)
             if el.tag == qn("w:p") and is_template_heading(element_text(el), letter_of)]
    blocks: dict[str, tuple[_Element, list[_Element]]] = {}
    for n, (i, text) in enumerate(heads):
        letter = letter_of(text)
        if letter and letter not in blocks:
            end = heads[n + 1][0] if n + 1 < len(heads) else len(children)
            blocks[letter] = (children[i], children[i + 1:end])
    return [text for _, text in heads], blocks


def _is_boilerplate(line: str, template_lines: frozenset[str]) -> bool:
    return (line in template_lines or is_template_instruction(line) or is_near_template_instruction(line)
            or is_unanswered_prompt(line) or is_template_label_line(line))


def _option_lines(elements: Iterable[_Element]) -> set[str]:
    """Colon-free body paragraphs: a "choose one" list's options (E). Table
    cells never count -- G's and J's row labels are not choices."""
    return {normalize_template_text(t) for t in (element_text(el) for el in elements if el.tag == qn("w:p"))
            if t and ":" not in t}


def is_unfilled(source: list[_Element], template: list[_Element]) -> bool:
    """The source block holds nothing but template text, so there is nothing to copy.

    A line with anything past the template's wording -- including a value
    typed after a label in the label cell ("Primary Hospital Affiliation:
    NewYork-Presbyterian") -- is content. A block of template lines only is
    still filled when it keeps some but not all of the template's option
    paragraphs: E's "choose one, delete the others" list, cut to the choice.
    """
    known = frozenset(block_lines(template))
    if any(not _is_boilerplate(line, known) for line in block_lines(source)):
        return False
    options = _option_lines(template)
    kept = options & _option_lines(source)
    return not (0 < len(kept) < len(options))


def clean_copy(el: _Element) -> _Element:
    """Deep copy of a source paragraph/table, safe to insert into the output.

    Tracked insertions accepted, deletions and comments dropped; references
    into the source package (styles, numbering, images, hyperlink targets,
    footnotes) removed so the output's own defaults apply."""
    copy = deepcopy(el)
    for node in list(copy.iter()):
        if node.tag in _DROP and node.getparent() is not None:
            node.getparent().remove(node)
    for node in list(copy.iter()):
        if node.tag in _UNWRAP and node.getparent() is not None:
            parent = node.getparent()
            at = parent.index(node)
            for child in list(node):
                parent.insert(at, child)
                at += 1
            parent.remove(node)
    for node in copy.iter():
        for attr in _ID_ATTRS:
            node.attrib.pop(attr, None)
        node.attrib.pop(qn("r:id"), None)
    return copy


def capture_source_blocks(path: str, letter_of: LetterOf) -> dict[str, list[_Element]]:
    """{letter: source elements} for the E/G/J blocks of a WCM-format source;
    {} when the source is not WCM-format. A block longer than
    MAX_BLOCK_ELEMENTS is left out (see its comment). Filled-or-not is the
    caller's call: it needs the template's own lines for that."""
    heading_texts, blocks = section_blocks(Document(path).element.body, letter_of)
    if not is_wcm_format(heading_texts):
        return {}
    captured = {}
    for letter, (_, elements) in blocks.items():
        if len(elements) > MAX_BLOCK_ELEMENTS:
            logger.warning("Source %s block has %d elements (> %d); not copied", letter, len(elements), MAX_BLOCK_ELEMENTS)
            continue
        captured[letter] = elements
    return captured
