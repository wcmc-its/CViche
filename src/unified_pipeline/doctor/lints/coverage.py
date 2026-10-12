"""Lints that account for the source document's text in the rendered output (#1588).

One responsibility: read the source CV and the rendered WCM document side by
side, and name source text that reached neither the page nor the Appendix.
The other loss lints each read one stage's artifact against the page
(`unrendered_records` stage 4, `dead_sections` stage 2, `segmentation` the
1a/2 cut); this one skips the stages and compares the two ends, so a record
lost at any stage shows up the same way.

`source_line_coverage` is the cheap first step of #1588's fact accounting:
word pairs, not facts. Normalising a fact that was legitimately reformatted
(5c teaching, 5d citations, dates) is the hard part #1588 leaves open.
"""
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import NamedTuple

from unified_pipeline.core.template_boilerplate import is_source_boilerplate
from unified_pipeline.core.text_norm import norm, squash
from unified_pipeline.stage6.normalization.pii import (
    SCOPE_PERSONAL_AND_APPENDIX,
    _pii_matches,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE
from unified_pipeline.stage6.sections.licensure import (
    _DEA_LABEL_RE,
    _FUSED_DEA_TOKEN_RE,
)

from ..shared import _finding

#: A word the check reads: three or more letters, after `norm`'s case and
#: accent folding. Digits and one- and two-letter tokens are left out on
#: purpose: stage 5d rewrites initials ("G.M." to "GM") and stage 6 rewrites
#: dates ("9/15/96" to "1996"), so a pair holding either would read a
#: legitimate reformat as a loss. Numbers are facts #1588's full accounting
#: still has to check.
_WORD_RE = re.compile(r"[^\W\d_]{3,}")

#: A source line is judged only when it holds this many word pairs (six
#: words). ponytail: fitted on the EBYSBC/s7ab/pilot farm (in-sample);
#: shorter lines are mostly headings, dates and labels the WCM template
#: rewords. Revisit with the full fact accounting (#1588).
COVERAGE_MIN_PAIRS = 5

#: A judged line is uncovered when under this share of its word pairs is
#: rendered (#1588's 0.5).
COVERAGE_MIN_SHARE = 0.5

#: A name-list line: at least this many comma- or semicolon-separated
#: chunks, and this share of them short runs of capitalised words or
#: initials. Stage 5d rewrites "Jane A. Doe, Richard Roe" as "Doe JA, Roe R",
#: so an author line split from its citation keeps its surnames and loses
#: every first name: a reformat, not a loss.
NAME_LIST_MIN_CHUNKS = 3
NAME_LIST_MIN_SHARE = 0.8
_NAME_CHUNK_MAX_WORDS = 4
_NAME_WORD_RE = re.compile(r"^(?:[A-Z][\w'’.-]*|and|&)$")

#: A citation's leading author list (#1588, YUY-SLC's commonest false
#: positive): stage 5d rewrites "Jane A. Doe, Richard Roe, Mei Chan" as
#: "Doe JA, Roe R, Chan M", so every first name and every surname-to-next-
#: name pair is lost, and a citation whose title renders whole still reads
#: under half covered. A leading run of name chunks is left out of the
#: pairs when one rendered line holds a word of at least
#: NAME_LIST_MIN_CHUNKS of its chunks of two or more words: the list was
#: rendered, reformatted, and the citation is judged on the rest of its line
#: (counting the list as rendered instead would let a long author list carry
#: a lost title). Not a word of every chunk: 5d cuts a long list to six names
#: and "et al.", and a two-letter surname is no word (in-sample HFAJCC 173,
#: PFBSNH 102). A chunk is a name chunk when it has 1 to
#: _NAME_CHUNK_MAX_WORDS lettered words, each capitalised (or "and", "&");
#: a list number, a footnote mark or a year is no word and does not break
#: it ("12. Doe JA", "Roe R*", "and Ada Sample (2023).").
_AUTHOR_CONNECTORS = frozenset({"and", "&"})
#: Marks around a name word that are not part of it.
_NAME_MARKS = "*†‡§¶()[]{}.,;:\"'“”‘’"
#: Where a chunk's names can end inside it: before " (" (a year or a
#: role) or at a period followed by a space or the end ("Roe R. Title").
_NAME_CUT_RE = re.compile(r"\s\(|\.(?=\s|$)")
#: A list marker ("c)", "(d)", "n.", "3)", "A)") opening a line: no name,
#: and no sign the line continues the one before it.
_LIST_MARKER_RE = re.compile(r"^\s*\(?(?:\d{1,3}|[a-z])[.)]\s+|^\s*\(?[A-Z]\)\s+")

#: A URL or an e-mail address. A record line is
#: judged on its words without them: 5d drops a citation's DOI or URL, and a
#: link's pieces ("https", "doi", "org") read as lost words when the record
#: rendered (#1588, YUY-SLC: in-sample WIANVH 868, one held-out hit). A line
#: that is little but a link keeps them: the link is what it holds.
_LINK_RE = re.compile(r'\b(?:https?:/*|www\.|mailto:)\S+|\S+@\S+\.\w+', re.IGNORECASE)
#: A Word HYPERLINK field code, the link target ahead of the text Word shows
#: for it: field syntax, never content, so it is always left out.
_FIELD_CODE_RE = re.compile(r'HYPERLINK\s+"[^"]*"')

#: A table's column-header row typed as tab-separated cells: at least this
#: many cells, every word capitalised (or a connector, so no number), and the
#: next printed line a tab-separated row holding a digit, its first data row
#: (#1588, YUY-SLC in-sample: BMHBJZ 1187, JNATFN 337, YYVHNN 472). The WCM
#: template prints its own column labels, never the CV's.
COLUMN_HEADER_MIN_CELLS = 3
_HEADER_CONNECTORS = frozenset({"of", "and", "&", "the", "in", "for", "per", "to", "or"})

#: A printed line that continues the one before it: it opens in lower case,
#: or closes a bracket or quote it never opened ("Edition), Edited by ...",
#: "Assessment,” co-presented with ..."). A citation or title wrapped by
#: hard line breaks is judged as one line, so its tail is not read as a
#: record of its own (#1588, YUY-SLC in-sample: XWNZWW 590/603/743).
_OPENERS, _CLOSERS = "([“", ")]”"

#: Source lines quoted per finding, as the other text lints quote.
COVERAGE_EVIDENCE_MAX = 3
COVERAGE_QUOTE_CHARS = 100


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(norm(text))


class _Output:
    """Which rendered lines hold each word. A source word pair is rendered
    when one output line holds both words, in either order: stage 6 and the
    formatters reorder a record's parts ("Member, Committee" becomes
    "Committee | Member") without losing them. Lines are read from the whole
    document, the Appendix and tracked deletions included, so content the
    Appendix holds, or that stage 6 struck through on purpose, is accounted
    for."""

    def __init__(self, blocks: Iterable[tuple[str, str]]) -> None:
        self._lines: dict[str, set[int]] = defaultdict(set)
        line_id = 0
        for _, text in blocks:
            for line in str(text).split("\n"):
                for word in _words(line):
                    self._lines[word].add(line_id)
                line_id += 1

    def renders(self, pair: tuple[str, str]) -> bool:
        first, second = pair
        return bool(self._lines.get(first, set()) & self._lines.get(second, set()))

    def most_groups_on_one_line(self, groups: list[list[str]]) -> int:
        """The most groups any one rendered line holds a word of."""
        counts: dict[int, int] = defaultdict(int)
        for group in groups:
            for line_id in set().union(*(self._lines.get(word, set()) for word in group)):
                counts[line_id] += 1
        return max(counts.values(), default=0)


def _name_chunk_words(chunk: str) -> list[str] | None:
    """A name chunk's lettered words, or None when the chunk is no name
    chunk (_AUTHOR_CONNECTORS above). A chunk with no lettered word (a
    year, a page range) is no name chunk either."""
    words = [w.strip(_NAME_MARKS) for w in chunk.split()]
    words = [w for w in words if any(c.isalpha() for c in w)]
    if not 1 <= len(words) <= _NAME_CHUNK_MAX_WORDS:
        return None
    if all(w[:1].isupper() or w in _AUTHOR_CONNECTORS for w in words):
        return words
    return None


def _leading_names(line: str) -> tuple[int, list[list[str]]]:
    """Where a line's leading run of name chunks ends, and the 3+-letter
    words of each chunk of two or more words in it. The last chunk may hold
    the names and then the title ("Roe R. Widget torsion ..."): it counts up
    to the longest cut (_NAME_CUT_RE) whose head is a name chunk."""
    start = (m.end() if (m := _LIST_MARKER_RE.match(line)) else 0)
    end, multi = start, []
    for chunk in re.finditer(r"[^,;]+", line[start:]):
        words = _name_chunk_words(chunk.group())
        if words is None:
            cuts = [c.start() for c in _NAME_CUT_RE.finditer(chunk.group())]
            heads = [c for c in cuts if _name_chunk_words(chunk.group()[:c])]
            if not heads:
                break
            words = _name_chunk_words(chunk.group()[:heads[-1]])
            end = start + chunk.start() + heads[-1]
        else:
            end = start + chunk.end()
        if len(words) >= 2:
            multi.append(_words(" ".join(w for w in words if w not in _AUTHOR_CONNECTORS)))
        if end < start + chunk.end():
            break
    return end, multi


def _rendered_authors_end(line: str, output: _Output) -> int:
    """Where the line's leading author list ends when the list was rendered
    (_AUTHOR_CONNECTORS above), else 0."""
    end, multi = _leading_names(line)
    if output.most_groups_on_one_line(multi) >= NAME_LIST_MIN_CHUNKS:
        return end
    return 0


def _is_column_header(line: str, next_line: str) -> bool:
    """A tab-separated column-header row over its data rows
    (COLUMN_HEADER_MIN_CELLS)."""
    cells = [c.strip() for c in line.split("\t") if c.strip()]
    if len(cells) < COLUMN_HEADER_MIN_CELLS:
        return False
    words = [w.strip(_NAME_MARKS) for c in cells for w in c.split()]
    if not all(w[:1].isupper() or w in _HEADER_CONNECTORS for w in words if w):
        return False
    return "\t" in next_line and any(c.isdigit() for c in next_line)


def _is_withheld_dea(line: str) -> bool:
    """A DEA registration with its number: stage 6 withholds it whole (#821,
    stage6/sections/licensure.py), so its absence is by design."""
    return bool(_DEA_LABEL_RE.search(line) and _FUSED_DEA_TOKEN_RE.search(line))


def _is_continuation(line: str) -> bool:
    """A printed line that continues the one before it (_OPENERS above).
    A list item (_LIST_MARKER_RE) never does."""
    if _LIST_MARKER_RE.match(line):
        return False
    text = line.lstrip()
    if text[:1].islower():
        return True
    first = next((c for c in text if c in _OPENERS or c in _CLOSERS), None)
    return first is not None and first in _CLOSERS


class _PrintedLine(NamedTuple):
    """One judged source line: `text`, with any continuation lines joined
    on, and `head`, its first printed line, which names its entry."""
    head: str
    text: str


def _printed_lines(source_lines: list[str]) -> list[_PrintedLine]:
    """The source's non-blank printed lines, a continuation line joined to
    the line before it."""
    lines: list[_PrintedLine] = []
    for paragraph in source_lines:
        for line in str(paragraph).split("\n"):
            if not line.strip():
                continue
            if lines and _is_continuation(line):
                lines[-1] = lines[-1]._replace(text=f"{lines[-1].text} {line.strip()}")
            else:
                lines.append(_PrintedLine(line, line))
    return lines


def _is_name_list(line: str) -> bool:
    """An author or person list (NAME_LIST_MIN_CHUNKS)."""
    chunks = [c.strip() for c in re.split(r"[,;]", line) if c.strip()]
    if len(chunks) < NAME_LIST_MIN_CHUNKS:
        return False
    names = sum(1 for c in chunks
                if len(c.split()) <= _NAME_CHUNK_MAX_WORDS
                and all(_NAME_WORD_RE.match(w.strip("()")) for w in c.split()))
    return names / len(chunks) >= NAME_LIST_MIN_SHARE


def _heading_texts(stage1a: dict | None) -> frozenset[str]:
    """Stage 1a's hierarchy node texts: the WCM template prints its own
    headings, not the CV's."""
    texts: set[str] = set()
    stack = list((stage1a or {}).get("hierarchy") or [])
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            texts.add(norm(node.get("text", "")).strip(" :"))
            stack.extend(node.get("children") or [])
    return frozenset(texts)


class _Entries:
    """Stage 3b's entries, to name the entry a source line sits in."""

    def __init__(self, stage3b: dict | None) -> None:
        self._entries = [(squash(e.get("text", "")), e.get("element_idx_start"), e.get("taxonomy_code"))
                         for e in (stage3b or {}).get("entries") or [] if isinstance(e, dict)]

    def find(self, line: str) -> tuple[int | None, str | None]:
        """(element_idx_start, taxonomy_code) of the first entry whose text
        holds the line, or (None, None) when no entry does."""
        piece = squash(line)
        for text, start, code in self._entries:
            if piece in text:
                return (int(start) if isinstance(start, (int, float)) else None), code
        return None, None


def _pairs(text: str, output: _Output) -> list[tuple[str, str]]:
    """Adjacent word pairs of the text after a rendered leading author list."""
    words = _words(text[_rendered_authors_end(text, output):])
    return list(zip(words, words[1:], strict=False))


def _rendered_share(line: str, next_line: str, headings: frozenset[str],
                    output: _Output) -> float | None:
    """The share of a source line's word pairs some rendered line holds, or
    None when the line is not judged: under COVERAGE_MIN_PAIRS pairs once a
    rendered leading author list is left out (5d reformats the list,
    _AUTHOR_CONNECTORS above), and its links too unless it holds little
    else (_LINK_RE), template boilerplate, a heading, a name
    list, a column-header row, or a protected value stage 6 withholds
    (personal data, a DEA registration)."""
    if (is_source_boilerplate(line) or norm(line).strip(" :") in headings
            or _is_name_list(line) or _is_column_header(line, next_line)
            or _is_withheld_dea(line) or _pii_matches(line, SCOPE_PERSONAL_AND_APPENDIX)):
        return None
    text = _FIELD_CODE_RE.sub(" ", line)
    pairs = _pairs(_LINK_RE.sub(" ", text), output)
    if len(pairs) < COVERAGE_MIN_PAIRS:
        pairs = _pairs(text, output)  # little but a link: the link is the content
    if len(pairs) < COVERAGE_MIN_PAIRS:
        return None
    return sum(map(output.renders, pairs)) / len(pairs)


def lint_source_line_coverage(source_lines: list[str], blocks: list[tuple[str, str]],
                              deleted_blocks: list[tuple[str, str]] | None = None,
                              stage1a: dict | None = None,
                              stage3b: dict | None = None) -> list[dict]:
    """Source lines under COVERAGE_MIN_SHARE of whose word pairs any rendered
    line holds, Appendix and tracked deletions included (#1588). One finding
    per stage-3b entry the lines sit in, led by its index so the scorer can
    match it, quoting up to COVERAGE_EVIDENCE_MAX of the lines; lines no
    entry holds share one finding with no index. A Personal Data (A) entry is
    not judged: its fixed slots reformat every value and withhold the home
    contact by design.

    INFO, and a review-copy note only, never a run-page row
    (REVIEW_COPY_ONLY_LINTS): held out on YUYVIG, 4 of a hand-checked 30 is
    wholly missing text and 15 of 30 loses something (doctor/PRECISION.md,
    YUY-SLC2), under the 80% WARN bar and #1625's 50% run-page bar."""
    output = _Output([*blocks, *(deleted_blocks or [])])
    headings = _heading_texts(stage1a)
    entries = _Entries(stage3b)
    by_entry: dict[int | None, list[tuple[str, float]]] = {}
    lines = _printed_lines(source_lines)
    for i, (head, line) in enumerate(lines):
        next_line = lines[i + 1].text if i + 1 < len(lines) else ""
        share = _rendered_share(line, next_line, headings, output)
        if share is None or share >= COVERAGE_MIN_SHARE:
            continue
        idx, code = entries.find(head)
        if code != PERSONAL_DATA_CODE:
            by_entry.setdefault(idx, []).append((line.strip(), share))
    findings = []
    for idx, hits in by_entry.items():
        where = f"entry {idx}: " if idx is not None else "no stage-3b entry: "
        lowest = min(share for _, share in hits)
        findings.append(_finding(
            "source_line_coverage", "INFO",
            f"{where}{len(hits)} source line(s) with under {COVERAGE_MIN_SHARE:.0%} of their "
            f"word pairs anywhere in the output, Appendix included (lowest {lowest:.0%})",
            [line[:COVERAGE_QUOTE_CHARS] for line, _ in hits[:COVERAGE_EVIDENCE_MAX]]))
    return findings
