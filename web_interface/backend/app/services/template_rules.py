"""Template rules: review-copy comments the WCM template's own asks imply (#1999).

Each rule in `template_rules.json` names a slot of the template (the start of
its heading or label text), a condition, an optional threshold and the comment
a reviewer reads. The rules are data, so WCM's reviewers can change the list
without code. The default list (Paul, 2026-10-30) is in #1999.

A rule reads the delivered document only, never the run's JSON. A slot is
found by text the template ships (`key_files/wcm_cv_template_faculty_october_2022_final.docx`,
whitespace-collapsed, case kept), so a content line that happens to start
"Abstracts" is never a heading. Its scope runs to the next non-blank paragraph
the template ships, or the Appendix. Content is any text in that scope the
template does not ship; stage 6's "N/A" (`NOT_APPLICABLE`, #1942) is content
of its own kind.

Conditions:
- ``max_entries``: more than ``threshold`` numbered entries ("12. ...") under the heading.
- ``max_words``: more than ``threshold`` words of content under the heading.
- ``always``: the heading is in the document.
- ``blank``: a form label with nothing written. A paragraph label still reads
  as the template ships it (stage 6 appends a value after a tab); a table label
  has only empty or template cells right of it in its row, or below it in its
  column when it heads one (Percent Effort).
- ``not_applicable``: the slot holds nothing but "N/A", or nothing at all.
- ``section_empty``: the same, over a whole top-level section, to the next one.

Nothing here edits the document: it returns where each comment goes.
"""
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.text.paragraph import Paragraph

from app.services import (
    quality_score_service,  # noqa: F401  (puts src/ on sys.path for unified_pipeline)
)
from unified_pipeline.core.template_boilerplate import (  # noqa: E402
    normalize_template_text,
    template_section_headers,
)
from unified_pipeline.quality_score import _TEMPLATE_DOCX_PATH  # noqa: E402
from unified_pipeline.stage6.empty_slots import (  # noqa: E402
    APPENDIX_HEADING,
    NOT_APPLICABLE,
)

RULES_PATH = Path(__file__).with_name("template_rules.json")
#: The lint a template rule's comment is recorded under in the review copy's
#: comment map; its shape is ``<condition>:<slot>``.
TEMPLATE_RULE_LINT = "template_rule"
#: The conditions a rule may name (the module docstring says what each tests).
CONDITIONS = frozenset({"always", "blank", "not_applicable", "section_empty",
                        "max_entries", "max_words"})
#: A bibliography entry as stage 6 numbers it: "12. Quill AB ...".
_NUMBERED_ENTRY_RE = re.compile(r"^\d+\.\s")


@dataclass(frozen=True)
class TemplateRule:
    """One row of `template_rules.json`."""
    slot: str
    condition: str
    comment: str
    threshold: int | None = None

    @property
    def shape(self) -> str:
        return f"{self.condition}:{self.slot}"


@dataclass(frozen=True)
class _Block:
    """One body element above the Appendix: a paragraph (``para`` set) or a table."""
    element: BaseOxmlElement
    para: Paragraph | None
    text: str  # whitespace-collapsed; "" for a table


def _text(element: BaseOxmlElement) -> str:
    """Every `w:t` under *element*, whitespace collapsed: tracked insertions
    in, deletions out."""
    return " ".join("".join(t.text or "" for t in element.iter(qn("w:t"))).split())


@cache
def _template_texts() -> tuple[frozenset[str], frozenset[str]]:
    """The template's own paragraph texts and table-cell texts, non-blank."""
    body = Document(str(_TEMPLATE_DOCX_PATH)).element.body
    paragraphs = frozenset(t for t in map(_text, body.iterchildren(qn("w:p"))) if t)
    cells = frozenset(t for t in map(_text, body.iter(qn("w:tc"))) if t)
    return paragraphs, cells


def _is_template_paragraph(block: _Block) -> bool:
    return block.para is not None and block.text in _template_texts()[0]


def _is_section_heading(block: _Block) -> bool:
    return (_is_template_paragraph(block)
            and normalize_template_text(block.text) in template_section_headers())


def _blocks(doc: DocumentObject) -> list[_Block]:
    """The body's paragraphs and tables, in order, up to the Appendix."""
    blocks = []
    for el in doc.element.body.iterchildren(qn("w:p"), qn("w:tbl")):
        block = _Block(el, Paragraph(el, doc._body), _text(el)) if el.tag == qn("w:p") else _Block(el, None, "")
        if block.text == APPENDIX_HEADING:
            break
        blocks.append(block)
    return blocks


def _heading(blocks: list[_Block], slot: str) -> int | None:
    """Where the first template paragraph starting with *slot* is."""
    return next((i for i, b in enumerate(blocks)
                 if _is_template_paragraph(b) and b.text.startswith(slot)), None)


def _scope(blocks: list[_Block], at: int, ends: Callable[[_Block], bool]) -> list[_Block]:
    """The blocks after *at* up to the first that *ends* the scope."""
    scope = []
    for block in blocks[at + 1:]:
        if ends(block):
            break
        scope.append(block)
    return scope


def _slot_ends(block: _Block) -> bool:
    return bool(block.text) and _is_template_paragraph(block)


def _content(scope: list[_Block]) -> Iterator[str]:
    """Every paragraph and cell text in *scope* the template does not ship."""
    paragraphs, cells = _template_texts()
    for block in scope:
        if block.para is not None:
            if block.text and block.text not in paragraphs:
                yield block.text
            continue
        yield from (t for t in map(_text, block.element.iter(qn("w:tc"))) if t and t not in cells)


def _only_not_applicable(scope: list[_Block]) -> bool:
    return all(text == NOT_APPLICABLE for text in _content(scope))


def _body_paragraphs(scope: list[_Block]) -> list[str]:
    return [b.text for b in scope if b.para is not None and b.text]


def _cell_rows(tbl: BaseOxmlElement) -> list[list[BaseOxmlElement]]:
    return [list(tr.iterchildren(qn("w:tc"))) for tr in tbl.iterchildren(qn("w:tr"))]


def _blank_cells(cells: list[BaseOxmlElement]) -> bool:
    template_cells = _template_texts()[1]
    return all(not _text(tc) or _text(tc) in template_cells for tc in cells)


def _table_label(tbl: BaseOxmlElement, slot: str) -> tuple[BaseOxmlElement, list[BaseOxmlElement]] | None:
    """The first cell of *tbl* starting with *slot*, and its answer cells: the
    rest of its row, or the cells below it when it heads a column."""
    rows = _cell_rows(tbl)
    for r, row in enumerate(rows):
        for c, tc in enumerate(row):
            if _text(tc).startswith(slot):
                answers = row[1:] if c == 0 and len(row) > 1 else [
                    below[c] for below in rows[r + 1:] if c < len(below)]
                return tc, answers
    return None


def _blank_label(blocks: list[_Block], slot: str, doc: DocumentObject) -> Paragraph | None:
    """The first label starting with *slot*, when nothing is written beside it."""
    for block in blocks:
        if block.para is not None:
            if _is_template_paragraph(block) and block.text.startswith(slot):
                return block.para
            continue
        found = _table_label(block.element, slot)
        if found is not None:
            label, answers = found
            return Paragraph(label.find(qn("w:p")), doc._body) if _blank_cells(answers) else None
    return None


def _over(count: int, rule: TemplateRule) -> bool:
    return rule.threshold is not None and count > rule.threshold


def _fires(rule: TemplateRule, blocks: list[_Block], at: int) -> bool:
    """Whether *rule*, whose heading is ``blocks[at]``, fires."""
    if rule.condition == "always":
        return True
    if rule.condition == "section_empty":
        return (_is_section_heading(blocks[at])
                and _only_not_applicable(_scope(blocks, at, _is_section_heading)))
    scope = _scope(blocks, at, _slot_ends)
    if rule.condition == "not_applicable":
        return _only_not_applicable(scope)
    texts = _body_paragraphs(scope)
    if rule.condition == "max_entries":
        return _over(sum(1 for t in texts if _NUMBERED_ENTRY_RE.match(t)), rule)
    return _over(sum(len(t.split()) for t in texts), rule)  # max_words


def _anchor(rule: TemplateRule, blocks: list[_Block], doc: DocumentObject) -> Paragraph | None:
    """Where *rule*'s comment goes in the document, or None when it does not fire."""
    if rule.condition == "blank":
        return _blank_label(blocks, rule.slot, doc)
    at = _heading(blocks, rule.slot)
    return blocks[at].para if at is not None and _fires(rule, blocks, at) else None


@cache
def load_rules(path: Path = RULES_PATH) -> tuple[TemplateRule, ...]:
    """The rules in *path*; raises ValueError on a condition no code reads,
    or a max_* rule with no threshold."""
    rules = tuple(TemplateRule(**row) for row in json.loads(path.read_text(encoding="utf-8")))
    for rule in rules:
        if rule.condition not in CONDITIONS:
            raise ValueError(f"template rule {rule.slot!r}: unknown condition {rule.condition!r}")
        if rule.condition.startswith("max_") and rule.threshold is None:
            raise ValueError(f"template rule {rule.slot!r}: {rule.condition} needs a threshold")
    return rules


def template_rule_comments(doc: DocumentObject,
                           rules: tuple[TemplateRule, ...] | None = None) -> list[tuple[Paragraph, TemplateRule]]:
    """Each rule that fires on *doc*, with the paragraph its comment goes on,
    in rule order. ``rules`` defaults to `load_rules()`."""
    blocks = _blocks(doc)
    placed = ((_anchor(rule, blocks, doc), rule) for rule in (load_rules() if rules is None else rules))
    return [(para, rule) for para, rule in placed if para is not None]
