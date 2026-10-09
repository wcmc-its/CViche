"""What a reviewer did at each review-copy comment, read from the corrected
copy they uploaded (#1654).

The review copy (`<uid>_wcm_review.docx`, written by the backend's
review_comments.py) marks each doctor finding as a Word comment on the text it
is about. The corrected copy shows what happened there:

    fixed          the anchored text changed: edited, deleted or moved away
                   (whether the comment was then resolved, deleted or kept)
    not_a_problem  the anchored text is unchanged and the comment is gone, or
                   resolved: Word's "Resolve" keeps it in comments.xml and
                   marks it done in word/commentsExtended.xml
    unknown        the comment is still there, unresolved, and the text is
                   unchanged

A reviewer may upload the clean document instead, or a review copy with every
comment deleted. Then no comment of the review copy's author is left to
follow, and a dismissed comment cannot be told from one that was never there:
an unchanged text is `unknown`, never `not_a_problem`, and
`FateReport.comments_tracked` says so.

How the two files line up: both are read as docx_diff.py reads a delivered
and a corrected file (accepted view, CViche note boxes skipped), and each
review-copy block is followed to the corrected block it became
(`docx_diff.block_counterparts`). A comment's anchored text is "unchanged"
when that block still holds it. A comment is "still there" when the
corrected copy has a comment by the same author with the same wording on that
block.

PII: the anchored text and the comment wording never leave this module. A
`CommentFate` is a comment id and a verdict.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from unified_pipeline.doctor.docx_diff import (
    _REMOVED_WRAPPERS,
    _W_NS,
    _W_T,
    Block,
    _collapse,
    block_counterparts,
    read_block_elements,
)
from unified_pipeline.doctor.precision import (
    REVIEW_FIXED,
    REVIEW_NOT_A_PROBLEM,
    REVIEW_UNKNOWN,
)

if TYPE_CHECKING:
    from docx.document import Document as DocumentType

_W_RANGE_START = f"{_W_NS}commentRangeStart"
_W_RANGE_END = f"{_W_NS}commentRangeEnd"
_W_ID = f"{_W_NS}id"
_W_P = f"{_W_NS}p"
_W14_PARA_ID = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
_W15_NS = "{http://schemas.microsoft.com/office/word/2012/wordml}"
#: The part where Word records a comment as resolved: a w15:commentEx with
#: w15:done="1" whose w15:paraId is the comment's last paragraph's w14:paraId.
COMMENTS_EXTENDED_PART = "/word/commentsExtended.xml"


#: A verdict as doctor_vs_autopsy.py's `doctor_review` reads it. `unknown`
#: is no verdict, so it is not written.
LABEL_VERDICTS = {REVIEW_FIXED: "TP", REVIEW_NOT_A_PROBLEM: "FP"}


@dataclass(frozen=True)
class Anchor:
    """One comment and the text it is on. ``label`` is the comment's own
    wording and ``text`` the anchored text, collapsed; neither leaves this
    module. ``block`` indexes the file's `read_blocks`; None when the comment
    has no range in a block that is read. ``resolved``: marked done in Word."""
    comment_id: int
    label: str
    block: int | None
    text: str
    resolved: bool = False


@dataclass(frozen=True)
class CommentFate:
    """One review-copy comment's verdict."""
    comment_id: int
    verdict: str


@dataclass(frozen=True)
class FateReport:
    """Every review-copy comment's verdict. ``comments_tracked`` is False when
    the corrected copy holds no comment by the review copy's author."""
    comments_tracked: bool
    fates: list[CommentFate]


def resolved_comment_ids(doc: DocumentType) -> set[int]:
    """The ids of the comments Word marks resolved (COMMENTS_EXTENDED_PART);
    none when the document has no such part."""
    from lxml import etree

    part = next((p for p in doc.part.package.iter_parts() if p.partname == COMMENTS_EXTENDED_PART), None)
    if part is None:
        return set()
    done = {e.get(f"{_W15_NS}paraId") for e in etree.fromstring(part.blob).iter(f"{_W15_NS}commentEx")
            if e.get(f"{_W15_NS}done") == "1"}
    resolved = set()
    for comment in doc.comments:
        paragraphs = list(comment._comment_elm.iter(_W_P))
        if paragraphs and paragraphs[-1].get(_W14_PARA_ID) in done:
            resolved.add(comment.comment_id)
    return resolved


def read_anchors(doc: DocumentType, author: str) -> tuple[list[Block], list[Anchor]]:
    """The document's blocks, and each comment by ``author`` with the block
    its range starts in, the accepted text inside its range and whether it
    is resolved."""
    labels = {c.comment_id: c.text for c in doc.comments if c.author == author}
    resolved = resolved_comment_ids(doc)
    blocks = read_block_elements(doc)
    start: dict[int, int] = {}
    texts: dict[int, list[str]] = {}
    open_ids: list[int] = []
    for n, (_block, element) in enumerate(blocks):
        for node in element.iter(_W_RANGE_START, _W_RANGE_END, _W_T):
            if node.tag == _W_T:
                if not any(a.tag in _REMOVED_WRAPPERS for a in node.iterancestors()):
                    for cid in open_ids:
                        texts[cid].append(node.text or "")
                continue
            cid = int(node.get(_W_ID))
            if node.tag == _W_RANGE_START:
                open_ids.append(cid)
                start.setdefault(cid, n)
                texts.setdefault(cid, [])
            elif cid in open_ids:
                open_ids.remove(cid)
    anchors = [Anchor(cid, label, start.get(cid), _collapse("".join(texts.get(cid, []))), cid in resolved)
               for cid, label in labels.items()]
    return [block for block, _element in blocks], anchors


def _verdict(anchor: Anchor, counterpart: int | None, after: list[Block],
             left: dict[tuple[int, str], list[bool]], tracked: bool) -> str:
    """One comment's verdict; ``left`` holds the corrected copy's comments
    by (block, wording) not yet claimed by an earlier comment, each as
    whether it is resolved."""
    # ponytail: a comment on a section heading (an Appendix diversion, a
    # finding with no quote) reads `fixed` only if the heading itself
    # changes, so moving entries into that section reads not_a_problem or
    # unknown. And a duplicate's two copies line up by difflib's earliest
    # match: deleting the earlier copy, not the flagged later one, reads as
    # the flagged copy unchanged. If the hand-checked sample (#1654) shows
    # either, judge heading comments by their section's blocks instead.
    if anchor.block is None or not anchor.text:
        return REVIEW_UNKNOWN
    if counterpart is None or anchor.text not in after[counterpart].text:
        return REVIEW_FIXED
    if not tracked:
        return REVIEW_UNKNOWN
    kept = left.get((counterpart, anchor.label))
    if kept:
        return REVIEW_NOT_A_PROBLEM if kept.pop(0) else REVIEW_UNKNOWN
    return REVIEW_NOT_A_PROBLEM


def comment_fates(review: Path, corrected: Path, author: str) -> FateReport:
    """The verdict of every comment by ``author`` in the review copy, read
    from the corrected copy. An unreadable file raises (python-docx's error)."""
    from docx import Document

    before, anchors = read_anchors(Document(str(review)), author)
    after, kept = read_anchors(Document(str(corrected)), author)
    counterparts = block_counterparts(before, after)
    left: dict[tuple[int, str], list[bool]] = {}
    for a in kept:
        if a.block is not None:
            left.setdefault((a.block, a.label), []).append(a.resolved)
    tracked = bool(kept)
    fates = [CommentFate(a.comment_id,
                         _verdict(a, counterparts[a.block] if a.block is not None else None,
                                  after, left, tracked))
             for a in anchors]
    return FateReport(tracked, fates)


def doctor_review(findings: list[dict]) -> list[dict]:
    """A verdict report's findings (lint, shape, severity, entry_index,
    verdict) as doctor_vs_autopsy.py `doctor_review` entries: `fixed` is TP,
    `not_a_problem` FP. An `unknown` verdict, and a finding whose lint is not
    known, are left out."""
    return [{"lint": f["lint"], "shape": f["shape"], "severity": f["severity"],
             "verdict": LABEL_VERDICTS[f["verdict"]], "element_idx_start": f["entry_index"]}
            for f in findings if f["verdict"] in LABEL_VERDICTS and f["lint"]]
