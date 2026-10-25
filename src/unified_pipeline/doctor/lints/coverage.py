"""Lints that account for the source document's text in the rendered output (#1588).

One responsibility: read the source CV and the rendered WCM document side by
side, and name source text that reached neither the page nor the Appendix.
The other loss lints each read one stage's artifact against the page
(`unrendered_records` stage 4, `dead_sections` stage 2, `segmentation` the
1a/2 cut); this one skips the stages and compares the two ends, so a record
lost at any stage shows up the same way.

`source_line_coverage` is the first step of #1588's fact accounting: each
judged source line is aligned one-to-one to its own place in the output, on
its facts and rare words (`_align`). Normalising every fact that was
legitimately reformatted (5c teaching, 5d citations, dates) is the part
#1588 leaves open.
"""
import copy
import heapq
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import NamedTuple

from unified_pipeline.core.template_boilerplate import is_source_boilerplate
from unified_pipeline.core.text_norm import norm, squash
from unified_pipeline.core.two_digit_year import expand_two_digit_year
from unified_pipeline.stage6.normalization.pii import (
    SCOPE_PERSONAL_AND_APPENDIX,
    _pii_matches,
)
from unified_pipeline.stage6.parsing.dates import (
    _MONTH_NAME_TO_NUM,
    _SEASON_TOKENS,
    CURRENT_DATE_VALUES,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE
from unified_pipeline.stage6.sections.licensure import (
    _DEA_LABEL_RE,
    _FUSED_DEA_TOKEN_RE,
)
from unified_pipeline.stage_4_5_research_summary import RESEARCH_ACTIVITIES_CODE

from ..shared import (
    TABLE_ROW_JOINER,
    SubpointText,
    _finding,
    normalise_hyphens,
    subpoint_owners,
)
from .summary import generated_summary

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


class _DeletedLine(NamedTuple):
    """One line of a tracked deletion (`deleted_blocks`): `block` its
    body-order block, and `owners` the entries it is the text of
    (`_deleted_line_owners`), None when no record says whose it is, so
    every source line may read it."""
    block: int
    text: str
    owners: frozenset | None


def _reads(owners: frozenset | None, entries: frozenset) -> bool:
    """Whether a source line that the stage-3b `entries` hold (their
    `element_idx_start`s, `_Entries.holding`) may be found in a deleted
    line that is the text of the entries `owners`. A tracked deletion is
    its own entry's struck text: a #1205 sub-point is source prose stage 6
    offers under its own entry, as `offered_as_subpoint` reads it, and the
    enrichment's w:del is the CV's own line of the citation it replaced. So
    it vouches for that entry's lines only: another entry's struck text
    sharing a line's facts is not where the line went (BMAMWE 1368, #1205
    comment 6388379186: another citation's enrichment w:del held the note
    once a sub-point took the claims that had held it). A deletion no
    record places (`owners` None) vouches for every line, as before; so
    does every deletion for a line no entry holds, whose own entry is
    unknown. A line stage 3b put in several entries is each one's. A record
    the CV lists twice, in two entries, shares one rendering in the
    accepted view (`_Judged.keys`) but not a tracked deletion: the owner's
    rule (#1205 comment 6398270589) is the line's own entry's deletions
    only, and a twin's would let another entry's deletion vouch again
    wherever `_same_record` reads two lines as one record."""
    return owners is None or not entries or not owners.isdisjoint(entries)


class _Output:
    """Which rendered lines hold each word. A source word pair is rendered
    when one output line holds both words, in either order: stage 6 and the
    formatters reorder a record's parts ("Member, Committee" becomes
    "Committee | Member") without losing them. Lines are read from the whole
    document, the Appendix and tracked deletions (`deleted`) included, so
    content the Appendix holds, or that stage 6 struck through on purpose,
    is accounted for; a tracked deletion only for its own entry's lines
    (`read_by`)."""

    def __init__(self, blocks: Iterable[tuple[str, str]], deleted: Iterable[_DeletedLine] = ()) -> None:
        self._lines: dict[str, set[int]] = defaultdict(set)
        #: Each deleted line's own entries, by line id (`_DeletedLine.owners`).
        self._owners: dict[int, frozenset] = {}
        #: The entries of the source line reading this output (`read_by`).
        self._reader: frozenset = frozenset()
        lines = [(line, None) for _, text in blocks for line in str(text).split("\n")]
        lines += [(d.text, d.owners) for d in deleted]
        for line_id, (line, owners) in enumerate(lines):
            for word in _words(line):
                self._lines[word].add(line_id)
            if owners is not None:
                self._owners[line_id] = owners

    def read_by(self, entries: frozenset) -> _Output:
        """This output as a source line the stage-3b `entries` hold reads
        it (`_reads`): another entry's struck text is not in it."""
        view = copy.copy(self)
        view._reader = entries
        return view

    def _holding(self, word: str) -> set[int]:
        """The lines holding `word` that this output's reader may read."""
        held = self._lines.get(word, set())
        if not self._owners:
            return held
        return {line_id for line_id in held if _reads(self._owners.get(line_id), self._reader)}

    def renders(self, pair: tuple[str, str]) -> bool:
        first, second = pair
        return bool(self._holding(first) & self._holding(second))

    def most_groups_on_one_line(self, groups: list[list[str]]) -> int:
        """The most groups any one rendered line holds a word of."""
        counts: dict[int, int] = defaultdict(int)
        for group in groups:
            for line_id in set().union(*(self._holding(word) for word in group)):
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
                return _entry_index(start), code
        return None, None

    def holding(self, line: str) -> frozenset[int]:
        """The element_idx_start of every entry whose text holds the line."""
        piece = squash(line)
        return frozenset(idx for text, start, _ in self._entries
                         if piece in text and (idx := _entry_index(start)) is not None)


def _entry_index(start: object) -> int | None:
    """A stage-3b entry's element_idx_start as an index; None when it is none."""
    return int(start) if isinstance(start, (int, float)) else None


def _subpoint_paragraphs(line: str, owners: Mapping[str, frozenset | None]) -> list[str] | None:
    """The #1205 sub-point paragraphs (`owners`, `subpoint_owners`) one
    deleted line is made of: the line itself, or a table row's joined line
    (TABLE_ROW_JOINER) made of nothing but them and the joiners, a spanning
    sub-point row's cell repeated per column. None when it is made of other
    text. Read by text, as `subpoint_text` places them: a deletion of a
    sub-point's very words would read as that sub-point (none on the
    102-run farm). The paragraphs are taken out of the whole line before
    its joiners, so a paragraph that itself holds the joiner (a duties
    line typed with " | ") is still read whole. ponytail: they are taken
    out longest first; a line they do not empty is read as other text."""
    key = squash(line)
    if key in owners:
        return [key]
    if TABLE_ROW_JOINER not in line:
        return None
    rest = key
    paragraphs: list[str] = []
    for paragraph in sorted(owners, key=len, reverse=True):
        if paragraph in rest:
            rest = rest.replace(paragraph, "")
            paragraphs.append(paragraph)
    if rest.replace(squash(TABLE_ROW_JOINER), ""):
        return None
    return paragraphs


def _deleted_line_owners(line: str, owners: Mapping[str, frozenset | None],
                         entries: _Entries) -> frozenset | None:
    """The entries one deleted line is the text of (`_reads`). For a
    sub-point (author SUBPOINT_AUTHOR, `docx_subpoint_lines`;
    `_subpoint_paragraphs`), the entries the render sidecar's placements
    name for every one of its paragraphs (`subpoint_owners`); None, every
    entry, without placements, as `offered_as_subpoint` reads them. For any
    other deletion, such as the enrichment's w:del of a citation's CV line,
    the stage-3b entries whose text holds it (`_Entries.holding`); None when
    none does, as for a formatter's fragment no entry holds verbatim.

    A sub-point's owners are the indexes the sidecar names, a source line's
    own entries the ones stage 3b names, and the two are not reconciled. A
    placement under an index that no stage-3b entry holding the line
    carries (another index, or None) vouches for none of that line, and so
    does a sub-point whose written text differs from its placement's
    paragraph, which no placement then names. On the 102-run farm every
    placement's index is a stage-3b entry's and every sub-point line is a
    placement's."""
    paragraphs = _subpoint_paragraphs(line, owners)
    if not paragraphs:
        return entries.holding(line) or None
    placed = [owners[paragraph] for paragraph in paragraphs]
    if any(entry_ids is None for entry_ids in placed):
        return None
    return frozenset.intersection(*placed)


def _deleted_lines(deleted_blocks: list[tuple[str, str]] | None, subpoints: SubpointText | None,
                   entries: _Entries) -> list[_DeletedLine]:
    """Every non-blank line of the tracked deletions, with the entries it is
    the text of (`_deleted_line_owners`): `subpoints` is the docx's
    sub-points (`docx_subpoint_lines`) with the render sidecar's placements."""
    owners = subpoint_owners(subpoints)
    return [_DeletedLine(b, line, _deleted_line_owners(line, owners, entries))
            for b, (_, text) in enumerate(deleted_blocks or [])
            for line in str(text).split("\n") if line.strip()]


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


# ------------------------------------------------- one-to-one alignment (#1588)
#
# `_rendered_share` above asks whether a line's word pairs sit on ANY
# rendered line. That reads a reworded row as lost (a month printed as a
# number, "Current" as "Present", a row label stage 6 never prints) and a
# lost record as rendered when a near-identical record kept its words. The
# lint now decides each judged line by aligning it to its own place in the
# output instead: its fact tokens, weighted by rarity in the CV, each output
# token spent by one record only. `_rendered_share` still decides which
# lines are judged, and still serves stage2_text_break unchanged.

#: A judged line is covered when its best output span holds at least this
#: share of its fact tokens, weighted by rarity (`_token_weights`), and
#: every year it states.
ALIGNED_MIN_SHARE = 0.5

#: A Word table of at most this many rows is also read as one span: stage 6
#: lays one record out as label/value rows ("Name: | ...", "Mentoring
#: Period: | 2001-2004"), so a source line's facts can sit in several rows.
ALIGN_TABLE_SPAN_ROWS = 12

#: A source line can also be read against a run of rows of a longer
#: table: stage 6 fans a list out one item per row ("Reviewer for: Journal
#: A, Journal B, ..."). A run is the rows around an anchor that each hold
#: one of the line's tokens of at least its mean weight, with at most
#: ALIGN_RUN_GAP rows between them that do not; a row holding only common
#: words ("Member", "Committee") does not extend it, so a lost line cannot
#: gather a whole table's words.
ALIGN_RUN_GAP = 1

#: A source line can also be read against this many consecutive rendered
#: paragraphs: stage 6 splits a record's title from its description or its
#: "Also covered in" line.
ALIGN_PARAGRAPH_SPAN = 2

#: A source line can also be read against several units anywhere in the
#: output, one per record it holds: stage 6 renders a line that lists
#: several talks or several years of one award ("Grand rounds, Example
#: Hospital, 2001, 2004 and 2009"; "Lecture, Example Polytechnic, 2001;
#: Example College, 2003") as one row per record, each in its place in the
#: list. A record group is one period the line states (a closed range's two
#: ends, or a year alone); its unit must state the group's years and hold
#: one of the line's tokens of at least its mean weight that is no year, so
#: a row holding the year alone vouches for nothing. A line of more than
#: this many groups is not read this way.
ALIGN_GROUP_MAX = 6
#: ... and the line must hold at least this share of the unit's own
#: source-known weight (its words, numbers and stated years), so the unit is
#: mostly this line's record and not another record that shares a year and
#: a few words with it: a citation's e-pub year does not borrow the
#: conference abstract of the same study, nor a society line's year another
#: society's committee row (a row whose own source line is not judged, a
#: name list say, claims nothing).
ALIGN_GROUP_MIN_OWN = 2 / 3

#: Month and season words: stage 6 prints a month as a number and drops a
#: season, so neither is a fact a reworded row must keep. The vocabularies
#: are stage 6's own date parser's.
_DATE_WORDS = frozenset(_MONTH_NAME_TO_NUM) | _SEASON_TOKENS

#: One of stage 6's open ends (`CURRENT_DATE_VALUES`: "current", "ongoing",
#: "now", "to date", ...) or "today" closing a date range ("2019-current",
#: "2012 to date"): stage 6 prints each as "Present". Read only right after
#: a digit and a dash or "to", never elsewhere: "now" and "Current" are
#: prose too ("Current Position", "now part of ...").
_OPEN_END_RE = re.compile(
    r"(?<=\d)(\s*(?:[-–—]|to|until|through)\s*)(?:"
    + "|".join(sorted(map(re.escape, CURRENT_DATE_VALUES | {"today"}), key=len, reverse=True))
    + r")\b", re.IGNORECASE)

#: A four-digit year, 1900 to 2099, standing alone.
_YEAR4_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
#: A page, not a year: a citation's "286(11):1919-26", "33:2015-21" or
#: "pp. 2015-21". A colon after a volume or issue only, so "Dates: 2001"
#: keeps its year.
_PAGE_RE = re.compile(r"(?:(?<=[\d)])\s*:\s*|\bpp?\.\s*)\d+(?:\s*-\s*\d+)?", re.IGNORECASE)
#: A two-digit year after an apostrophe ("July '96").
_APOSTROPHE_YEAR_RE = re.compile(r"['‘’](\d{2})(?!\d)")
#: A month/year date ("07/96"), not a month/day one ("3/14/06", "3/31,
#: 1988"). In a source line it must also be no month/day: its two digits
#: are no day (over 31), or it is one end of a month/year range
#: ("12/23-12/24"), so "3/30-4/2/1995" states no 2030. A rendered line's is
#: stage 6's own mm/yy format ("08/17") and is read as it stands.
_SLASH_YEAR_RE = re.compile(r"(?<![\d/])(?:0?[1-9]|1[0-2])/(\d{2})(?![\d/])(?!,?\s*(?:19|20)\d{2})")
_SLASH_RANGE_RE = re.compile(
    r"(?<![\d/])(?:0?[1-9]|1[0-2])/(\d{2})\s*-\s*(?:0?[1-9]|1[0-2])/(\d{2})(?![\d/])")
#: The last day a month/day date can name, so the highest two digits that
#: may be a day rather than a year.
_MAX_DAY = 31
#: A rendered year range, "1998-2015" or "1998 to 2015": stage 6 prints a
#: list of years as one range (span_count is that lint's question), so a
#: rendered line's range holds every year inside it, for a source line
#: aligned to that one line only: a span of several lines must state the
#: line's years, or a lost line would find its year inside any range.
_RENDERED_RANGE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:-|to|through|until)\s*((?:19|20)\d{2})(?!\d)", re.IGNORECASE)
#: The longest rendered range read as holding its inner years.
ALIGN_RANGE_MAX_YEARS = 60
#: A year range written short, "2007-08" or "2013-5": a dash only, so a
#: citation's "2010, 33:1047" states no 2033, and neither end inside a
#: month/day date, so "7/2010-6/2011" and "9/1/1990-6/30/1991" state no
#: 2016 or 1996 (`_range_shorthand_ends` reads both, for implausible_year).
_YEAR_RANGE_SHORT_RE = re.compile(
    rf"(?<![\d/.])((?:19|20)\d{{2}})\s*-\s*(\d{{1,2}})(?![\d/.-])(?!\s*(?:{'|'.join(sorted(_DATE_WORDS, key=len, reverse=True))})\b)",
    re.IGNORECASE)
#: A number of this many digits or more, years apart, is a fact (a grant or
#: licence number, an amount, a page): shorter ones are days, months, counts
#: and list numbers stage 6 reformats or drops.
ALIGN_NUMBER_MIN_DIGITS = 3
_NUMBER_RE = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])|\d+")
#: A list number opening a line ("142.", "(12)"): the source's and stage 6's
#: numbering differ, so it is no fact of the record.
_LIST_NUMBER_RE = re.compile(r"^\s*\(?\d{1,4}[.)]\s+")
#: Token prefixes: a year, a number, a link. Words are letters only.
_YEAR_TOKEN, _NUMBER_TOKEN, _LINK_TOKEN = "y:", "#", "@"


def _short_ranges(text: str) -> list[tuple[int, int]]:
    """The year ranges written short (_YEAR_RANGE_SHORT_RE), as (first,
    last): "2007-08" is (2007, 2008), "1998-02" (1998, 2002)."""
    ranges = []
    for start, tail in _YEAR_RANGE_SHORT_RE.findall(text):
        unit = 10 ** len(tail)
        end = int(start) // unit * unit + int(tail)
        ranges.append((int(start), end + unit if end < int(start) else end))
    return ranges


#: A year opening a period, closed or open ("2008-2019", "2019-", "2019 -
#: June 2020", "2019-present").
_RANGE_START_RE = re.compile(r"(?<![\d/])(?:19|20)\d{2}\s*-")


def _year_ranges(text: str) -> tuple[list[tuple[int, int]], bool]:
    """The closed year ranges a text states, "1998-2004", "1998 to 2004"
    or "2007-08", as (first, last), and whether it opens any period
    (_RANGE_START_RE); a citation's pages are neither."""
    text = _PAGE_RE.sub(" ", normalise_hyphens(text))
    ranges = [(int(a), int(b)) for a, b in _RENDERED_RANGE_RE.findall(text)
              if 0 < int(b) - int(a) <= ALIGN_RANGE_MAX_YEARS]
    ranges += _short_ranges(text)
    return ranges, bool(ranges) or bool(_RANGE_START_RE.search(text))


def _record_groups(years: frozenset[str], text: str) -> tuple[frozenset[str], ...]:
    """A judged line's record groups (ALIGN_GROUP_MAX), from the year
    tokens it is aligned on: each closed range the text states (`_year_ranges`)
    as its two ends, each other year alone, in year order."""
    groups, left = [], set(years)
    for first, last in _year_ranges(text)[0]:
        ends = {f"{_YEAR_TOKEN}{first}", f"{_YEAR_TOKEN}{last}"}
        if ends <= left:
            groups.append(frozenset(ends))
            left -= ends
    groups += [frozenset({year}) for year in left]
    return tuple(sorted(groups, key=sorted))


def _page_years(text: str) -> set[int]:
    """The page numbers a rendered citation prints (_PAGE_RE) that read
    as years, a short last page written out ("1984-90" is 1984 and 1990):
    a source citation that gives its pages without a volume's colon
    ("8: 2, 1984-1990, 2003") states them as years."""
    years = set()
    for match in _PAGE_RE.finditer(text):
        numbers = re.findall(r"\d+", match.group())
        if numbers and len(numbers[-1]) < len(numbers[0]):
            numbers[-1] = numbers[0][:len(numbers[0]) - len(numbers[-1])] + numbers[-1]
        years |= {int(n) for n in numbers if _YEAR4_RE.fullmatch(n)}
    return years


def _stated_years(text: str, rendered: bool = False, inner: bool = False) -> set[int]:
    """The years a text states: four digits, a short range's end
    ("2007-08"), or a two-digit year after an apostrophe or a month
    (_SLASH_YEAR_RE, read as stage 6 writes it when `rendered`); and, when
    `inner`, every year inside a range (_RENDERED_RANGE_RE). A citation's
    pages are no years (_PAGE_RE), but a rendered citation's pages hold
    the source's (`_page_years`)."""
    text = normalise_hyphens(text)
    pages = _page_years(text) if rendered else set()
    text = _PAGE_RE.sub(" ", text)
    years = pages | {int(y) for y in _YEAR4_RE.findall(text)}
    if inner:
        for start, end in _RENDERED_RANGE_RE.findall(text):
            if 0 < int(end) - int(start) <= ALIGN_RANGE_MAX_YEARS:
                years |= set(range(int(start), int(end) + 1))
    years.update(end for _, end in _short_ranges(text))
    two_digit = {int(yy) for yy in _APOSTROPHE_YEAR_RE.findall(text)}
    two_digit |= {int(yy) for yy in _SLASH_YEAR_RE.findall(text) if rendered or int(yy) > _MAX_DAY}
    two_digit |= {int(yy) for pair in _SLASH_RANGE_RE.findall(text) for yy in pair}
    return years | {expand_two_digit_year(yy) for yy in two_digit}


def _link_key(link: str) -> str:
    """A link as one token: no scheme, "www." or trailing punctuation."""
    key = re.sub(r"^(?:https?:/*|mailto:)", "", link.lower())
    return _LINK_TOKEN + key.removeprefix("www.").rstrip("/.,;:)]>\"'")


def _fact_tokens(text: str, links: bool = True, rendered: bool = False) -> Counter:
    """The tokens one text is aligned on, its list number left out: its
    words of three or more letters, month and season words left out and a
    closing "current" read as "present"; each year it states ("y:1996");
    each number of ALIGN_NUMBER_MIN_DIGITS or more ("#1250000"); and, when
    `links`, each link as one token; years as `_stated_years` reads them,
    a rendered line's (`rendered`) as stage 6 writes them, the years inside
    its ranges included. Counted, so a rendered line that holds a year twice
    can vouch for two records."""
    text = _OPEN_END_RE.sub(r"\1present", _LIST_NUMBER_RE.sub("", text))
    tokens = Counter(w for w in _words(text) if w not in _DATE_WORDS)
    tokens.update(f"{_YEAR_TOKEN}{y}" for y in _stated_years(text, rendered, inner=rendered))
    for match in _NUMBER_RE.findall(text):
        digits = match.replace(",", "").lstrip("0")
        if len(digits) >= ALIGN_NUMBER_MIN_DIGITS and not _YEAR4_RE.fullmatch(digits):
            tokens[f"{_NUMBER_TOKEN}{digits}"] += 1
    if links:
        tokens.update(_link_key(m) for m in _LINK_RE.findall(text))
    return tokens


#: Unit kinds: a paragraph, a table row (its cells joined), a tracked-
#: deletion line, and the research summary the model wrote (`_output_units`).
_PARAGRAPH, _ROW, _DELETED, _SUMMARY = "p", "row", "deleted", "summary"


class _Unit(NamedTuple):
    """One rendered line the source is aligned to: `kind` one of
    _PARAGRAPH, _ROW, _DELETED or _SUMMARY; `block` is its body-order
    block; `owners` a deleted line's own entries (`_DeletedLine`)."""
    block: int
    kind: str
    text: str
    owners: frozenset | None = None


def _table_units(text: str, rows: list[list[str]] | None) -> list[str]:
    """A table's rows, each its distinct non-empty cells joined, plus any
    line of the block no row holds (a nested table's). Without its rows,
    the block's own lines."""
    lines = [line for line in str(text).split("\n") if line.strip()]
    if rows is None:
        return lines
    units = []
    for row in rows:
        cells: list[str] = []
        for cell in row:
            cell = " ".join(str(cell).split())
            if cell and cell not in cells:
                cells.append(cell)
        if cells:
            units.append(TABLE_ROW_JOINER.join(cells))
    held = squash(" ".join(units))
    return units + [line for line in lines if squash(line) not in held]


def _output_units(blocks: list[tuple[str, str]], deleted: list[_DeletedLine],
                  table_rows: list[list[list[str]]] | None, summary: str = "") -> list[_Unit]:
    """Every rendered line in the accepted view (`blocks`: tracked
    insertions in, deletions out), Appendix included, then each tracked
    deletion's line (`deleted`): a sub-point or a struck original stage 6
    offers the reviewer. Tables are read row by row when `table_rows` (the
    same document's top-level tables, in order) matches the blocks. The
    research summary the model wrote (`summary`) is its own kind: stage
    4.5 wrote it in place of the CV's research activities (M1), so it
    renders those lines and paraphrases other records' without rendering
    them (`_Judged.narrative`)."""
    tables = sum(kind == "table" for kind, _ in blocks)
    rows = iter(table_rows) if table_rows is not None and len(table_rows) == tables else None
    summary_key = squash(summary)
    units = []
    for b, (kind, text) in enumerate(blocks):
        if kind == "table":
            units += [_Unit(b, _ROW, t) for t in _table_units(text, next(rows) if rows else None)]
        else:
            units += [_Unit(b, _SUMMARY if summary_key and squash(line) == summary_key else _PARAGRAPH, line)
                      for line in str(text).split("\n") if line.strip()]
    units += [_Unit(line.block, _DELETED, line.text, line.owners) for line in deleted]
    return units


def _spans(units: list[_Unit]) -> list[list[tuple[int, ...]]]:
    """Per unit, the fixed spans that hold it: itself, its whole table when
    the table has at most ALIGN_TABLE_SPAN_ROWS rows, and each run of up to
    ALIGN_PARAGRAPH_SPAN consecutive paragraphs it sits in. Runs of a
    longer table's rows and one unit per record group depend on the line
    (`_Alignment._row_runs`, `_Alignment._group_covers`)."""
    spans: list[list[tuple[int, ...]]] = [[(u,)] for u in range(len(units))]
    rows_of: dict[int, list[int]] = defaultdict(list)
    for u, unit in enumerate(units):
        if unit.kind == _ROW:
            rows_of[unit.block].append(u)
    for members in rows_of.values():
        if 1 < len(members) <= ALIGN_TABLE_SPAN_ROWS:
            for u in members:
                spans[u].append(tuple(members))
    paragraphs = [u for u, unit in enumerate(units) if unit.kind == _PARAGRAPH]
    for size in range(2, ALIGN_PARAGRAPH_SPAN + 1):
        for k in range(len(paragraphs) - size + 1):
            run = tuple(paragraphs[k:k + size])
            if run[-1] - run[0] == size - 1:
                for u in run:
                    spans[u].append(run)
    return spans


class _Judged(NamedTuple):
    """A judged source line and what it is aligned on: its tokens with
    their weights, heaviest first, and the years it states. `entry` is the
    record it claims rendered tokens for; `keys` the records whose claims
    it may also read (itself, and the same record listed elsewhere:
    `_same_record`). `narrative` marks a research-activities (M1) line:
    only such a line may be aligned to the research summary, the
    paragraph stage 4.5 wrote in their place. `groups` are its record
    groups (`_record_groups`, ALIGN_GROUP_MAX). `own` is the
    `element_idx_start` of each stage-3b entry that holds it
    (`_Entries.holding`): the tracked deletions it may read (`_reads`)."""
    i: int
    entry: object
    keys: frozenset
    weighted: tuple[tuple[str, float], ...]
    total: float
    years: frozenset[str]
    narrative: bool = False
    groups: tuple[frozenset[str], ...] = ()
    own: frozenset = frozenset()

    def anchors(self) -> list[str]:
        """The heaviest tokens, down to the first that takes their weight
        past 1 - ALIGNED_MIN_SHARE of the total: a span holding none of
        them holds under ALIGNED_MIN_SHARE, so only spans through a unit
        holding one can cover the line."""
        heavy, weight = [], 0.0
        for token, w in self.weighted:
            heavy.append(token)
            weight += w
            if weight > (1 - ALIGNED_MIN_SHARE) * self.total:
                break
        return heavy


class _Match(NamedTuple):
    """How well one span holds one line: every year held, the weighted
    share of the line's tokens it holds, the share of the span's own
    source-known weight the line explains (how specifically the span is
    this line's), and the span. Ordered best first by `rank`."""
    years_held: bool
    share: float
    exact: float
    span: tuple[int, ...]

    @property
    def covered(self) -> bool:
        return _covered(self.years_held, self.share)

    def rank(self) -> tuple:
        """A covering span beats any other, and a covering single unit
        beats a covering span of several (a record's own row or paragraph
        before a run that borrows a neighbour's); then years and share,
        and only then the fewest units, so among spans of several units a
        line goes to its own record's rows, not to fewer rows of a
        neighbour's that hold less of it; then specificity."""
        covered = self.covered
        return (covered, covered and len(self.span) == 1, self.years_held, self.share,
                -len(self.span) if covered else 0, self.exact)


_NO_MATCH = _Match(False, 0.0, 0.0, ())


class _Alignment:
    """Source lines aligned one-to-one to the rendered units (#1588): each
    line to the span that holds it best (`_Match.rank`), a token of a unit
    spent by one record only. Lines are placed best match first, ties in
    source order, so a rendered record claims its own line before a
    near-identical lost one can, and the lost one is left with what no
    other record holds."""

    def __init__(self, units: list[_Unit], weights: dict[str, float]) -> None:
        self._units = units
        self._tokens = [_fact_tokens(unit.text, rendered=True) for unit in units]
        self._stated = [frozenset(f"{_YEAR_TOKEN}{y}" for y in _stated_years(unit.text, rendered=True))
                        for unit in units]
        self._weights = weights
        self._spans = _spans(units)
        self._postings: dict[str, list[int]] = defaultdict(list)
        for u, tokens in enumerate(self._tokens):
            for token in tokens:
                self._postings[token].append(u)
        self._span_tokens: dict[tuple[int, ...], tuple[frozenset[str], float]] = {}
        self._claims: dict[tuple[int, str], set] = {}
        self._claimed_units: set[int] = set()

    def _held(self, span: tuple[int, ...]) -> tuple[frozenset[str], float]:
        """The span's tokens, and their source-known weight. A span of
        several units holds only the years they state (_RENDERED_RANGE_RE)."""
        if span not in self._span_tokens:
            tokens = frozenset().union(*(self._tokens[u] for u in span))
            if len(span) > 1:
                tokens = frozenset(t for t in tokens if not t.startswith(_YEAR_TOKEN)).union(
                    *(self._stated[u] for u in span))
            self._span_tokens[span] = (tokens, sum(self._weights.get(t, 0.0) for t in tokens))
        return self._span_tokens[span]

    def _readable(self, line: _Judged, u: int, summary: bool) -> bool:
        """Whether unit u can hold the line: the research summary only when
        `summary`, and a tracked deletion only when it is the line's own (`_reads`)."""
        unit = self._units[u]
        return (summary or unit.kind != _SUMMARY) and _reads(unit.owners, line.own)

    def _free(self, u: int, token: str, line: _Judged) -> bool:
        claimed = self._claims.get((u, token))
        if not claimed:
            return token in self._tokens[u]
        return not claimed.isdisjoint(line.keys) or len(claimed) < self._tokens[u][token]

    def _match(self, line: _Judged, span: tuple[int, ...]) -> _Match:
        held, span_weight = self._held(span)
        if self._claimed_units.isdisjoint(span):
            found = {token for token, _ in line.weighted if token in held}
        else:
            found = {token for token, _ in line.weighted if token in held
                     and any(self._free(u, token, line) for u in span)}
        weight = sum(w for token, w in line.weighted if token in found)
        known = sum(self._weights.get(token, 0.0) for token in found)
        return _Match(line.years <= found, weight / line.total if line.total else 0.0,
                      known / span_weight if span_weight else 0.0, span)

    def _row_runs(self, line: _Judged, anchors: list[int]) -> set[tuple[int, ...]]:
        """The runs of table rows through each anchor row (ALIGN_RUN_GAP)."""
        mean = line.total / len(line.weighted)
        strong = {u for token, w in line.weighted if w >= mean for u in self._postings.get(token, ())}
        runs = set()
        for anchor in anchors:
            if self._units[anchor].kind != _ROW:
                continue
            ends = []
            for step in (-1, 1):
                end, gap, u = anchor, 0, anchor + step
                while (0 <= u < len(self._units) and self._units[u].kind == _ROW
                       and self._units[u].block == self._units[anchor].block):
                    if u in strong:
                        end, gap = u, 0
                    elif (gap := gap + 1) > ALIGN_RUN_GAP:
                        break
                    u += step
                ends.append(end)
            if ends[1] > ends[0]:
                runs.add(tuple(range(ends[0], ends[1] + 1)))
        return runs

    def _own_share(self, u: int, tokens: set[str]) -> float:
        """The share of unit u's own source-known weight (its tokens, years
        as it states them) that `tokens` hold."""
        own = frozenset(t for t in self._tokens[u] if not t.startswith(_YEAR_TOKEN)) | self._stated[u]
        weight = sum(self._weights.get(t, 0.0) for t in own)
        return sum(self._weights.get(t, 0.0) for t in own if t in tokens) / weight if weight else 0.0

    def _group_covers(self, line: _Judged) -> set[tuple[int, ...]]:
        """The units, one per record group of the line (ALIGN_GROUP_MAX),
        that state the group's years and hold most of the line's still-free
        weight, among the line's own units: each holds one of its tokens of
        at least its mean weight that is no year, and the line holds
        ALIGN_GROUP_MIN_OWN of the unit's own weight. None when a group has
        no such unit."""
        if not 2 <= len(line.groups) <= ALIGN_GROUP_MAX:
            return set()
        mean = line.total / len(line.weighted)
        mine = {token for token, _ in line.weighted}
        holders = {u for token, w in line.weighted if w >= mean and not token.startswith(_YEAR_TOKEN)
                   for u in self._postings.get(token, ()) if self._readable(line, u, summary=False)}
        own = [u for u in sorted(holders) if self._own_share(u, mine) >= ALIGN_GROUP_MIN_OWN]
        cover = set()
        for group in line.groups:
            held = [(sum(w for token, w in line.weighted if self._free(u, token, line)), -u) for u in own
                    if group <= self._stated[u] and all(self._free(u, year, line) for year in group)]
            if not held:
                return set()
            cover.add(-max(held)[1])
        return {tuple(sorted(cover))} if len(cover) > 1 else set()

    def best(self, line: _Judged) -> _Match:
        """The line's best span (`_Match.rank`), the earliest on a tie;
        `_NO_MATCH` when no span can reach ALIGNED_MIN_SHARE
        (_Judged.anchors). The spans: the fixed ones (`_spans`), the runs
        of rows (`_row_runs`) and one unit per record group
        (`_group_covers`). The research summary is a span for a
        research-activities line only (`_Judged.narrative`), and a
        tracked deletion for its own entry's lines only (`_reads`)."""
        if not line.weighted:
            return _NO_MATCH
        anchors = sorted({u for token in line.anchors() for u in self._postings.get(token, ())
                          if self._readable(line, u, summary=line.narrative)})
        best = _NO_MATCH
        spans = ({span for u in anchors for span in self._spans[u]} | self._row_runs(line, anchors)
                 | self._group_covers(line))
        for span in sorted(spans, key=lambda s: (len(s), s)):
            held, _ = self._held(span)
            bound = sum(w for token, w in line.weighted if token in held) / line.total
            if _Match(line.years <= held, bound, 1.0, span).rank() <= best.rank():
                continue
            match = self._match(line, span)
            if match.rank() > best.rank():
                best = match
        return best

    def claim(self, line: _Judged, span: tuple[int, ...]) -> None:
        """Spend one of each of the line's tokens in the span, from the
        units that hold most of the line first, so a whole-table span
        takes the line's own row before a neighbour's. The research
        summary is never spent: it paraphrases all of the research
        activities at once, not one record each."""
        tokens = {token for token, _ in line.weighted}
        order = sorted((u for u in span if self._units[u].kind != _SUMMARY),
                       key=lambda u: (-len(tokens & self._tokens[u].keys()), u))
        for token in tokens:
            for u in order:
                if self._free(u, token, line):
                    self._claims.setdefault((u, token), set()).add(line.entry)
                    self._claimed_units.add(u)
                    break


#: The weighted share of a judged line's tokens another line must hold for
#: the two to be one record listed twice (`_same_record`).
ALIGN_SAME_RECORD_SHARE = 0.9


class _Listing(NamedTuple):
    """A judged line as `_same_record` compares it: its tokens, the years
    it states, its closed year ranges and whether it opens a period
    (`_year_ranges`)."""
    tokens: Counter
    years: frozenset[int]
    ranges: tuple[tuple[int, int], ...]
    period: bool


def _listing(tokens: Counter, text: str) -> _Listing:
    years = frozenset(int(t[len(_YEAR_TOKEN):]) for t in tokens if t.startswith(_YEAR_TOKEN))
    ranges, period = _year_ranges(text)
    return _Listing(tokens, years, tuple(ranges), period)


def _years_agree(small: _Listing, large: _Listing) -> bool:
    """The smaller listing states no year, or the larger's very years, or
    one of the two states single years only, inside one range the other
    states ("2001 reviewer" and "2001-2015 reviewer"). Two periods must
    match: "2010-2021 co-Director" and "2021- Director" are two posts, not
    one."""
    if not small.years or small.years == large.years:
        return True
    return any(all(first <= y <= last for y in inner.years)
               for inner, outer in ((small, large), (large, small)) if inner.years and not inner.period
               for first, last in outer.ranges)


def _same_record(a: _Listing, b: _Listing, weights: dict[str, float], unseen: float) -> bool:
    """Two judged lines that are one record listed twice ("Publications
    Committee, Example Society" and "Example Society Publications
    Committee (2014-Current)"): the smaller holds no word or number the
    larger lacks, by at least ALIGN_SAME_RECORD_SHARE of its weight, and
    their years agree (`_years_agree`). Stage 6 renders such a record
    once, so the two spend one claim. "Flywheel design medal, 2005" is not
    "Flywheel design medal, 2005 and 2007": their years differ, and neither
    states a range."""
    small, large = sorted((a, b), key=lambda c: sum(weights.get(t, unseen) for t in c.tokens))
    if not _years_agree(small, large):
        return False
    small, large = small.tokens, large.tokens
    total = sum(weights.get(t, unseen) for t in small)
    held = sum(weights.get(t, unseen) for t in small if t in large)
    return bool(total) and held / total >= ALIGN_SAME_RECORD_SHARE


#: A source heading the template prints its own words for (#1588 round 2:
#: the blind judges' NOT_CONTENT class). A stage-1a heading after a list
#: number ("7. COMMITTEES"), or followed by a bracketed legend that
#: states no year or number ("Lectures (speaker marked with a dagger)");
#: the first line of a heading wrapped over two (at least
#: HEADING_PREFIX_MIN_WORDS of its words); a digit-free lead-in of at most
#: HEADING_LEAD_IN_MAX_WORDS words with no sentence punctuation but the
#: colon it ends in ("Referee for these journals:"), whose facts
#: are the lines after it (a citation's "... Medicine. In:" or a "Title:
#: ...:" row is no lead-in); and, outside every stage-3b entry, a
#: digit-free line in capitals (stage 2's caps heading).
HEADING_PREFIX_MIN_WORDS = 3
HEADING_LEAD_IN_MAX_WORDS = 15
_HEADING_LEGEND_RE = re.compile(r"^[\s:–—-]*\([^()]*\)[\s:.]*$")
_HEADING_EDGE_CHARS = " :*-_="
_LEAD_IN_RE = re.compile(r"^(?:[^.:;()]|\([^()]*\))+:\s*$")


def _states_a_fact_number(text: str) -> bool:
    """Whether a text states a year or a number of ALIGN_NUMBER_MIN_DIGITS
    or more digits (`_fact_tokens`)."""
    return any(t.startswith((_YEAR_TOKEN, _NUMBER_TOKEN)) for t in _fact_tokens(text, links=False))


def _is_heading_line(line: str, headings: frozenset[str], in_entry: bool) -> bool:
    """A source heading or lead-in line (HEADING_PREFIX_MIN_WORDS above),
    against stage 1a's `headings` (`_heading_texts`); `in_entry` when a
    stage-3b entry holds the line."""
    unmarked = _LIST_MARKER_RE.sub("", line, count=1)
    text = " ".join(norm(unmarked).split()).strip(_HEADING_EDGE_CHARS)
    if not text:
        return False
    if text in headings:
        return True
    words = len(text.split())
    if not any(c.isdigit() for c in text):
        if not in_entry and line.strip().isupper():
            return True
        if _LEAD_IN_RE.match(unmarked) and words <= HEADING_LEAD_IN_MAX_WORDS:
            return True
        if words >= HEADING_PREFIX_MIN_WORDS and any(h.startswith(f"{text} ") for h in headings):
            return True
    return any(text.startswith(h) and _HEADING_LEGEND_RE.match(text[len(h):])
               and not _states_a_fact_number(text[len(h):]) for h in headings)


def _covered(years_held: bool, share: float) -> bool:
    return years_held and share >= ALIGNED_MIN_SHARE


def _token_weights(lines: list[_PrintedLine]) -> tuple[dict[str, float], float]:
    """A token's weight, its inverse document frequency over the CV's
    printed source lines (log(N / lines holding it)): a row label or a
    template word repeated down a list counts little, a name, a title word
    or a year one record states counts most. Also the weight of a token no
    printed line holds."""
    df = Counter(token for line in lines for token in set(_fact_tokens(line.text)))
    n = len(lines) + 1
    return {token: math.log(n / k) for token, k in df.items()}, math.log(n)


class _LineVerdict(NamedTuple):
    """One printed source line as source_line_coverage judges it."""
    i: int
    text: str
    idx: int | None
    code: str | None
    judged: bool
    share: float | None
    years_held: bool | None
    hit: bool


def _aligned_tokens(line: str, output: _Output) -> Counter:
    """A judged line's tokens: past a rendered leading author list (5d
    reformats it, _AUTHOR_CONNECTORS), without Word field codes, and
    without links unless the line holds little else (_LINK_RE)."""
    text = _FIELD_CODE_RE.sub(" ", line)
    text = text[_rendered_authors_end(text, output):]
    bare = _LINK_RE.sub(" ", text)
    if len(_pairs(bare, output)) >= COVERAGE_MIN_PAIRS:
        return _fact_tokens(bare, links=False)
    return _fact_tokens(text)


class _Aligned(NamedTuple):
    """A source CV aligned to its rendered document: the printed lines,
    each line's (entry index, taxonomy code), the judged lines, where each
    was placed, and the rendered units the spans index."""
    lines: list[_PrintedLine]
    where: list[tuple[int | None, str | None]]
    judged: dict[int, _Judged]
    placed: dict[int, _Match]
    units: list[_Unit]


def _negated(rank: tuple) -> tuple:
    """A `_Match.rank` turned into a min-heap key: best first."""
    return tuple(-float(x) for x in rank)


def _record_keys(listings: dict[int, _Listing], entry_of: dict[int, object],
                 weights: dict[str, float], unseen: float) -> dict[int, frozenset]:
    """Per judged line, the records whose claims it may read: its own, and
    every record one of whose lines is the same record listed twice as one
    of its own (`_same_record`), both ways: a record whose title wraps onto
    a second printed line that opens a sentence, and that the CV lists
    again elsewhere, is one record on every one of those lines. Candidates
    share the smaller line's heaviest token."""
    partners: dict[object, set] = defaultdict(set)
    by_heaviest: dict[str, list[int]] = defaultdict(list)
    by_token: dict[str, list[int]] = defaultdict(list)
    for i, listing in listings.items():
        tokens = listing.tokens
        heaviest = max(tokens, key=lambda t: (weights.get(t, unseen), t), default=None)
        candidates = set(by_token.get(heaviest, ())) | {j for t in tokens for j in by_heaviest.get(t, ())}
        for j in sorted(candidates):
            if entry_of[j] == entry_of[i]:
                continue
            if _same_record(listing, listings[j], weights, unseen):
                partners[entry_of[i]].add(entry_of[j])
                partners[entry_of[j]].add(entry_of[i])
        if heaviest is not None:
            by_heaviest[heaviest].append(i)
        for t in tokens:
            by_token[t].append(i)
    return {i: frozenset({entry_of[i]} | partners[entry_of[i]]) for i in listings}


def _align(source_lines: list[str], blocks: list[tuple[str, str]],
           deleted_blocks: list[tuple[str, str]] | None, stage1a: dict | None,
           stage3b: dict | None, table_rows: list[list[list[str]]] | None,
           stage_4_5: dict | None = None, subpoints: SubpointText | None = None) -> _Aligned:
    """Judge each printed line as `_rendered_share` does (A entries and
    headings left out: `_is_heading_line`), then place the judged lines one
    at a time, best match first (_Alignment), re-scoring a line whose span
    another record claimed since it was queued. A line reads only its own
    entry's tracked deletions and those no record places (`_reads`)."""
    entries = _Entries(stage3b)
    deleted = _deleted_lines(deleted_blocks, subpoints, entries)
    output = _Output(blocks, deleted)
    headings = _heading_texts(stage1a)
    lines = _printed_lines(source_lines)
    units = _output_units(blocks, deleted, table_rows,
                          generated_summary(stage_4_5) if isinstance(stage_4_5, dict) else "")
    weights, unseen = _token_weights(lines)
    alignment = _Alignment(units, weights)
    where: list[tuple[int | None, str | None]] = []
    held_by: dict[int, frozenset[int]] = {}
    tokens_of: dict[int, Counter] = {}
    for i, (head, line) in enumerate(lines):
        next_line = lines[i + 1].text if i + 1 < len(lines) else ""
        idx, code = entries.find(head)
        where.append((idx, code))
        held_by[i] = entries.holding(head)
        seen = output.read_by(held_by[i])
        if (code != PERSONAL_DATA_CODE and _rendered_share(line, next_line, headings, seen) is not None
                and not _is_heading_line(line, headings, idx is not None or code is not None)):
            tokens_of[i] = _aligned_tokens(line, seen)
    entry_of = {i: ("entry", where[i][0]) if where[i][0] is not None else ("line", i) for i in tokens_of}
    keys = _record_keys({i: _listing(tokens, lines[i].text) for i, tokens in tokens_of.items()},
                        entry_of, weights, unseen)
    judged: dict[int, _Judged] = {}
    queue = []
    for i, tokens in tokens_of.items():
        weighted = tuple(sorted(((t, weights.get(t, unseen)) for t in tokens), key=lambda tw: (-tw[1], tw[0])))
        years = frozenset(t for t in tokens if t.startswith(_YEAR_TOKEN))
        judged[i] = _Judged(i, entry_of[i], keys[i], weighted, sum(w for _, w in weighted), years,
                            where[i][1] == RESEARCH_ACTIVITIES_CODE, _record_groups(years, lines[i].text),
                            held_by[i])
        heapq.heappush(queue, (_negated(alignment.best(judged[i]).rank()), i))
    placed: dict[int, _Match] = {}
    while queue:
        key, i = heapq.heappop(queue)
        match = alignment.best(judged[i])
        if _negated(match.rank()) > key:
            heapq.heappush(queue, (_negated(match.rank()), i))
            continue
        placed[i] = match
        if match.covered:
            alignment.claim(judged[i], match.span)
    return _Aligned(lines, where, judged, placed, units)


def _line_verdicts(source_lines: list[str], blocks: list[tuple[str, str]],
                   deleted_blocks: list[tuple[str, str]] | None = None,
                   stage1a: dict | None = None, stage3b: dict | None = None,
                   table_rows: list[list[list[str]]] | None = None,
                   stage_4_5: dict | None = None,
                   subpoints: SubpointText | None = None) -> list[_LineVerdict]:
    """Every printed source line with its verdict (`_align`): a judged
    line is a hit when its span holds under ALIGNED_MIN_SHARE of its
    weighted tokens, or misses a year it states."""
    aligned = _align(source_lines, blocks, deleted_blocks, stage1a, stage3b, table_rows, stage_4_5,
                     subpoints)
    verdicts = []
    for i, line in enumerate(aligned.lines):
        idx, code = aligned.where[i]
        place = aligned.placed.get(i)
        verdicts.append(_LineVerdict(
            i, line.text, idx, code, place is not None,
            place.share if place else None, place.years_held if place else None,
            place is not None and not place.covered))
    return verdicts


def lint_source_line_coverage(source_lines: list[str], blocks: list[tuple[str, str]],
                              deleted_blocks: list[tuple[str, str]] | None = None,
                              stage1a: dict | None = None,
                              stage3b: dict | None = None,
                              table_rows: list[list[list[str]]] | None = None,
                              stage_4_5: dict | None = None,
                              subpoints: SubpointText | None = None) -> list[dict]:
    """Source lines not found in their own place in the output (#1588):
    aligned one-to-one to the rendered document (accepted view, Appendix
    and tracked deletions included), a line is reported when its span holds
    under ALIGNED_MIN_SHARE of its weighted fact tokens or misses a year it
    states (`_line_verdicts`). A tracked deletion counts only for its own
    entry's lines (`_reads`): a #1205 sub-point's entry is the one the
    render sidecar places it under (`subpoints`, the docx's sub-points with
    those placements), any other deletion's the stage-3b entry whose text
    holds it. One finding per stage-3b entry the lines sit in, led by its
    index so the scorer can match it, quoting up to COVERAGE_EVIDENCE_MAX of
    the lines; lines no entry holds share one finding with no index. A
    Personal Data (A) entry is not judged: its fixed slots reformat every
    value and withhold the home contact by design; nor is a heading or
    lead-in (`_is_heading_line`). A research-activities (M1) line may be
    found in the research summary stage 4.5 wrote in their place.

    INFO, and a review-copy note only, never a run-page row
    (REVIEW_COPY_ONLY_LINTS); its precision is doctor/PRECISION.md's
    SLC-A3, and SLC-OWN measures the own-entry reading of deletions."""
    by_entry: dict[int | None, list[tuple[str, float]]] = {}
    for verdict in _line_verdicts(source_lines, blocks, deleted_blocks, stage1a, stage3b, table_rows,
                                  stage_4_5, subpoints):
        if verdict.hit:
            by_entry.setdefault(verdict.idx, []).append((verdict.text.strip(), verdict.share))
    findings = []
    for idx, hits in by_entry.items():
        where = f"entry {idx}: " if idx is not None else "no stage-3b entry: "
        lowest = min(share for _, share in hits)
        findings.append(_finding(
            "source_line_coverage", "INFO",
            f"{where}{len(hits)} source line(s) not found in their own place in the output, "
            f"Appendix included: under {ALIGNED_MIN_SHARE:.0%} of their facts and rare words, "
            f"or a year missing (lowest {lowest:.0%})",
            [line[:COVERAGE_QUOTE_CHARS] for line, _ in hits[:COVERAGE_EVIDENCE_MAX]]))
    return findings
