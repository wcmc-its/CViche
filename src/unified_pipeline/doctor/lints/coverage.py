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

from unified_pipeline.core.template_boilerplate import is_source_boilerplate
from unified_pipeline.core.text_norm import norm, squash
from unified_pipeline.stage6.normalization.pii import (
    SCOPE_PERSONAL_AND_APPENDIX,
    _pii_matches,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE

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


def _judged(line: str, headings: frozenset[str]) -> list[tuple[str, str]]:
    """The word pairs a source line is judged on, or [] when it is not
    judged: too short, template boilerplate, a heading, a name list, or a
    protected personal-data value stage 6 withholds."""
    words = _words(line)
    pairs = list(zip(words, words[1:], strict=False))
    if (len(pairs) < COVERAGE_MIN_PAIRS or is_source_boilerplate(line)
            or norm(line).strip(" :") in headings or _is_name_list(line)
            or _pii_matches(line, SCOPE_PERSONAL_AND_APPENDIX)):
        return []
    return pairs


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
    (REVIEW_COPY_ONLY_LINTS): held out on YUYVIG, fewer than half of a
    hand-checked sample is wholly missing text (doctor/PRECISION.md,
    YUY-SLC), under the 80% WARN bar and #1625's 50% run-page bar."""
    output = _Output([*blocks, *(deleted_blocks or [])])
    headings = _heading_texts(stage1a)
    entries = _Entries(stage3b)
    by_entry: dict[int | None, list[tuple[str, float]]] = {}
    for paragraph in source_lines:
        for line in str(paragraph).split("\n"):
            pairs = _judged(line, headings)
            if not pairs:
                continue
            share = sum(map(output.renders, pairs)) / len(pairs)
            if share >= COVERAGE_MIN_SHARE:
                continue
            idx, code = entries.find(line)
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
