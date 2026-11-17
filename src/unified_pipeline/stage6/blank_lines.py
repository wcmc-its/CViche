"""Even out the blank paragraphs around the WCM template's headings (#2140).

The template spaces its sections with blank paragraphs, and each section
writer adds or consumes some as it fills its slot, so the delivered CV's gaps
depended on which writer ran and on whether a slot held content or "N/A":
"Books:" had no blank line before "N/A" and one before a citation, RESEARCH
followed the last Clinical bullet with no gap in 7 of 65 held-out CVs, and
removing the instruction box left three blank paragraphs under the title.

`even_out_blank_lines` runs once, after everything else has been written and
removed, and changes blank paragraphs only, above the Appendix:

* no run of blank paragraphs is longer than `MAX_BLANK_RUN`;
* a top-level section heading has `BLANKS_BEFORE_SECTION` above it (the
  template's own figure for every one of them);
* a paragraph-slot heading has `BLANKS_UNDER_HEADING` between it and what was
  written under it, "N/A" or content alike;
* a top-level heading that content follows directly gets that one blank too.

A heading is matched only on a paragraph the template itself shipped, as in
`empty_slots`. A blank paragraph is one with nothing in it at all
(`_is_spacer`): a paragraph that carries a section break, a bookmark, a
comment anchor or a tracked deletion is never counted or removed.

No `stage_6_word_template` import (a back-edge, `stage6/__init__.py`).
"""
from __future__ import annotations

from copy import deepcopy

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement

from ..core.template_boilerplate import (
    normalize_template_text,
    template_section_headers,
)
from .empty_slots import (
    APPENDIX_HEADING,
    PARAGRAPH_SLOT_PREFIXES,
    TemplateSnapshot,
    _heading_text,
    _text,
)

#: The longest run of blank paragraphs left anywhere above the Appendix.
MAX_BLANK_RUN = 2
#: Blank paragraphs above a top-level section heading, as the template ships.
BLANKS_BEFORE_SECTION = 2
#: Blank paragraphs between a heading and what was written under it.
BLANKS_UNDER_HEADING = 1

#: In `template_section_headers`, but a subsection of RESEARCH in this
#: template: one blank above it, not two.
_SUBSECTION_HEADINGS = frozenset({"Research Support:"})

_SECTION_HEADERS = template_section_headers()


def _is_spacer(element: BaseOxmlElement) -> bool:
    """True for a paragraph with nothing in it: no child but its own
    properties and runs that hold only run properties. A `w:sectPr` in the
    properties is a section break, so that paragraph is not one."""
    if element.tag != qn("w:p"):
        return False
    for child in element:
        if child.tag == qn("w:pPr"):
            if child.find(qn("w:sectPr")) is not None:
                return False
        elif child.tag != qn("w:r") or any(part.tag != qn("w:rPr") for part in child):
            return False
    return True


def _spacers(start: BaseOxmlElement, *, preceding: bool) -> list[BaseOxmlElement]:
    """The unbroken run of spacers directly before or after *start*, nearest first."""
    run = []
    for sibling in start.itersiblings(preceding=preceding):
        if not _is_spacer(sibling):
            break
        run.append(sibling)
    return run


#: Word's per-paragraph ids (`w14:paraId`, `w14:textId`) live in this namespace
#: and must not repeat, so a copied spacer drops them.
_W14_NAMESPACE = "{http://schemas.microsoft.com/office/word/2010/wordml}"


def _new_spacer(like: BaseOxmlElement | None) -> BaseOxmlElement:
    """A copy of *like*, so the new gap is as tall as its neighbours, or a bare paragraph."""
    if like is None:
        return OxmlElement("w:p")
    spacer = deepcopy(like)
    for name in [name for name in spacer.attrib if name.startswith(_W14_NAMESPACE)]:
        del spacer.attrib[name]
    return spacer


def _set_gap(heading: BaseOxmlElement, want: int, model: BaseOxmlElement | None, *,
             above: bool) -> int:
    """Leave exactly *want* spacers directly above or below *heading*. Returns
    how many paragraphs were added or removed."""
    spacers = _spacers(heading, preceding=above)
    for extra in spacers[want:]:
        extra.getparent().remove(extra)
    like = spacers[0] if spacers else model
    for _ in range(want - len(spacers)):
        spacer = _new_spacer(like)
        if above:
            heading.addprevious(spacer)
        else:
            heading.addnext(spacer)
    return abs(want - len(spacers))


def _written_paragraph_follows(heading: BaseOxmlElement, snapshot: TemplateSnapshot) -> bool:
    """True when the first thing under *heading* that is not a spacer is a
    paragraph a section wrote: "N/A" or content, not a table and not the
    template's own instruction line."""
    following = next((el for el in heading.itersiblings() if not _is_spacer(el)), None)
    if following is None or following.tag != qn("w:p"):
        return False
    return snapshot.paragraphs.get(following) != _text(following)


def _cap_blank_runs(elements: list[BaseOxmlElement]) -> int:
    """Remove every spacer past the `MAX_BLANK_RUN`th of a run. Returns the count removed."""
    removed = run = 0
    for element in elements:
        run = run + 1 if _is_spacer(element) else 0
        if run > MAX_BLANK_RUN:
            element.getparent().remove(element)
            removed += 1
    return removed


def _above_appendix(body: BaseOxmlElement) -> list[BaseOxmlElement]:
    elements = []
    for element in body.iterchildren():
        if element.tag == qn("w:p") and _heading_text(_text(element)) == APPENDIX_HEADING:
            break
        elements.append(element)
    return elements


def even_out_blank_lines(body: BaseOxmlElement, snapshot: TemplateSnapshot) -> int:
    """Apply the module's four rules to *body* above the Appendix. Returns how
    many blank paragraphs were added or removed."""
    elements = _above_appendix(body)
    changed = _cap_blank_runs(elements)
    model = next((el for el in elements if _is_spacer(el) and el.getparent() is not None), None)
    for element in elements:
        if element not in snapshot.paragraphs or element.getparent() is None:
            continue
        heading = _heading_text(snapshot.paragraphs[element])
        is_section = (normalize_template_text(heading) in _SECTION_HEADERS
                      and heading not in _SUBSECTION_HEADINGS)
        if is_section and element.getprevious() is not None:
            changed += _set_gap(element, BLANKS_BEFORE_SECTION, model, above=True)
        # A slot heading gets exactly one blank; a section heading keeps the
        # gap it has unless content sits directly under it.
        is_slot = heading.startswith(PARAGRAPH_SLOT_PREFIXES)
        runs_on = is_section and not _spacers(element, preceding=False)
        if (is_slot or runs_on) and _written_paragraph_follows(element, snapshot):
            changed += _set_gap(element, BLANKS_UNDER_HEADING, model, above=False)
    return changed
