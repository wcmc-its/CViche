"""Lints for how the source document was cut into a hierarchy (#493).

One responsibility: whether stage 1a/2 preserved the SHAPE of the source --
every line accounted for, entries the size of a record rather than a chapter,
and every header-looking line actually promoted to a hierarchy node.

The lints read the source lines and the stage 1a/2 artifacts. A header
demoted to content misroutes everything filed under it long before stage 6
runs, and the finding is about the cut, not the page. Two exceptions:
`lint_header_consumed_record` and `lint_stage2_text_break` take the rendered
blocks, when present, only to confirm that the text they name renders
nowhere.

Five lints read stage 2's own record of the cut (NECRGC autopsy, #1226
forensics, #1433): elements it assigned to no entry, header or break
(`lint_stage2_unaccounted`, #1693), a list grouped one record off
(`lint_record_date_shift`, #1226), a heading line that also holds a record
(`lint_header_consumed_record`, #1254), a break that holds text no entry
claimed (`lint_stage2_text_break`, #1226), and two entries that hold the
same lines (`lint_stage2_span_overlap`, #1433).

`lint_segmentation_collapse` (EBYSBC E17) reads stage 1b as well: the cut
can lose every heading while every line is still covered, which none of the
coverage metrics above can see.

`MISSED_HEADERS_WARN_COUNT`, `_hierarchy_titles` and `_header_key` are used by
nothing else in `run_doctor.py`, so they move here and stop being
module-global. `_magnitude_severity` is shared with the extraction domain and
stays in `doctor.shared`. Bodies are unmodified; `run_doctor` re-exports every
name it exported before.
"""

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import NamedTuple

from unified_pipeline.core.docx_structure_extractor import _is_date_only_text
from unified_pipeline.core.text_norm import fold_quotes, norm, squash
from unified_pipeline.segmentation_regression import (
    compute_metrics,
    find_lost_blocks,
    lint_metrics,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE

from ..shared import _finding, _haystacks, _magnitude_severity
from .coverage import (
    COVERAGE_MIN_SHARE,
    _heading_texts,
    _Output,
    _printed_lines,
    _rendered_share,
)

# --------------------------------------------------------------------------
# Coverage, lost lines, mega-entries and dups, via the regression metrics.


def lint_segmentation(source_lines: list[str], stage1a: dict,
                      stage2: dict) -> list[dict]:
    """Coverage / lost lines / mega-entries / dups / empties, reusing the
    segmentation_regression metrics (source docx + stage 1a + stage 2)."""
    metrics = compute_metrics(source_lines, stage1a, stage2)
    findings = []
    for flag in lint_metrics(metrics):
        evidence = ([line[:100] for line in metrics["lost_lines"][:5]]
                    if flag.startswith("coverage") else [])
        findings.append(_finding("segmentation", "WARN", flag, evidence))
    return findings


def lint_table_lost(source_block_lines: list[tuple[int, str]],
                    stage2: dict) -> list[dict]:
    """Source tables stage 2 mostly lost, however well the rest of the
    document covered (#815): web207 lost its whole personal-data table at
    99.1% document coverage. One finding per run, not per table -- a run
    whose stage 2 failed loses dozens of tables, and a per-table count
    would double as severity (#438). Evidence is the worst table's lines.

    A body-level content control that stage 2 mostly lost gets its own
    finding, worded for a control (#1656: stage 1 skipped RSFOYB's whole
    889-paragraph control and nothing flagged it). Still one per run for
    each kind of container, never one per control."""
    lost_blocks = find_lost_blocks(source_block_lines, stage2)
    findings = []
    for kind, noun in (("table", "source table(s)"),
                       ("content_control", "body content control(s)")):
        lost = [b for b in lost_blocks if b["kind"] == kind]
        if not lost:
            continue
        worst = max(lost, key=lambda b: len(b["lost_lines"]))
        findings.append(_finding(
            "table_lost", "WARN",
            f"{len(lost)} {noun} mostly lost; worst: "
            f"{len(worst['lost_lines'])} of {worst['substantive_lines']} lines",
            [line[:100] for line in worst["lost_lines"][:5]]))
    return findings


# --------------------------------------------------------------------------
# Header-looking source lines that never became a hierarchy node.
#
# The threshold is the measured p75 of headers-missed-per-run over the
# 73 scored runs of the 2026-07-25 batch, the same measurement as the
# other magnitudes. WARN means this run sits in the corpus's worst
# quartile; the corpus median is 2, and a flat WARN on any single demoted
# header is what made the verdict carry no information (#438).
MISSED_HEADERS_WARN_COUNT = 6

#: Severity of a finding whose only claim to being a header is its Heading
#: style, i.e. a line that is not ALL-CAPS (#1232). Many CVs style record
#: titles, journal names and contact-block lines Heading 1-6: on one batch
#: 42 of 75 findings (30 of them one CV's record titles) were such lines and
#: none was a real miss. Other CVs style their real sub-section labels the
#: same way and stage 1a misses some of them, so dropping the class would
#: lose real findings. Too ambiguous to drop, too weak to count toward the
#: run's WARN threshold: it is reported, never escalated.
WEAK_CANDIDATE_SEVERITY = "INFO"


def _hierarchy_titles(stage1a: dict) -> list[str]:
    titles: list[str] = []

    def walk(nodes):
        for node in nodes or []:
            title = norm(node.get("text", ""))
            if title:
                titles.append(title)
            walk(node.get("children"))

    walk(stage1a.get("hierarchy"))
    return titles


#: A leading enumeration token stripped before comparing header keys (#814):
#: roman ('I.', 'XI.'), arabic ('1.', '1.1.'), or a single letter ('A.').
#: Applied to lowercase, already-`norm`ed text -- 'I.' below is 'i.'.
#: Stage 1a promotes 'I.  CURRENT POSITION' to the hierarchy node
#: 'CURRENT POSITION', without the numeral; comparing raw normalized forms
#: reports every one of those headers as missing (web199: 11 of 11).
_ENUM_PREFIX_RE = re.compile(r"^(?:[ivxlc]+\.\s*|\d+(?:\.\d+)*\.?\s*|[a-z]\.\s*)")

#: Guard for the prefix/suffix match below: a short key ('and') is a prefix
#: or suffix of countless unrelated titles and must not match that way (#814)
#: -- only an exact `_header_key` hit counts for a candidate this short.
MIN_HEADER_KEY_CHARS = 12
MIN_HEADER_KEY_WORDS = 2

#: Second guard (round-2 fix for F2): a candidate must cover at least this
#: fraction of the matched title's length, not just clear the absolute-length
#: guard above. Without it a genuinely unrelated but coincidentally wordy
#: candidate silences itself against any longer title it happens to trail --
#: web200's standalone bold label 'UK GOVERNMENT' (14 chars, 2 words -- past
#: MIN_HEADER_KEY_CHARS/WORDS) is a real suffix of the unrelated H1
#: 'EXPERIENCE IN WORKING WITH UK GOVERNMENT' (41 chars, ratio 0.34) and was
#: silenced as a false negative. The wrapped-header case this matching exists
#: for keeps a high ratio: one physical line of 'SERVICE ON NATIONAL GRANT
#: REVIEW PANELS, STUDY SECTIONS,' is 58 of the 68-char joined title (0.85).
MIN_KEY_TO_TITLE_RATIO = 0.5


#: A leading bullet or asterisk marker stripped before comparing header keys
#: (#1232): the source line '*TENURE REVIEWS' is promoted by stage 1a as the
#: hierarchy node 'TENURE REVIEWS'. Applied to `norm`ed text.
_LEADING_MARKER_RE = re.compile(r"^[*\u2022\u00b7\u25aa\u25a0\u25cf\u25cb\u25e6]+\s*")

#: A trailing empty-value suffix stripped before comparing header keys
#: (#1232): 'SPECIALTY BOARD STATUS: N/A' is promoted as 'SPECIALTY BOARD
#: STATUS'. The suffix is a value, not part of the header name. Applied to
#: `norm`ed text, so 'n/a' is lowercase here.
_EMPTY_VALUE_SUFFIX_RE = re.compile(r"\s*:\s*n/a$")

#: Similarity (`difflib` ratio) at which a header wrapped over two source
#: lines counts as a known title that stage 1a copy-edited while joining the
#: lines (#1232): 1a corrected a one-letter typo in the first line, so the
#: joined text is no longer an exact, prefix or suffix copy of its title.
WRAPPED_HEADER_MIN_SIMILARITY = 0.95


def _header_key(text: str) -> str:
    """Comparison key for header matching: typographic quotes folded,
    normalized, a leading bullet/asterisk marker and a trailing ': N/A' value
    dropped, trailing ':' dropped, then a leading enumeration token stripped
    (#814, #1232).

    Stage 1a promotes 'PROFESSIONAL SOCIETIES:' to the hierarchy node
    'PROFESSIONAL SOCIETIES' -- the colon is source formatting, not part of the
    header name. Comparing raw normalized forms reports a header that WAS
    detected as missing: on the 2026-07-15 corpus (25 CVs) that was 36 of 61
    findings (59%), including 22 of web061's 23. The same is true of a leading
    'I.'/'1.'/'A.' section numeral: stage 1a strips it, so comparing the raw
    forms reports every enumerated section as missing too. A curly apostrophe
    in the source against an ASCII one in the node ('LOCAL (CONT'D)'), a
    leading '*' and a trailing ': N/A' are the same class (#1232).
    """
    normed = norm(fold_quotes(text))
    normed = _LEADING_MARKER_RE.sub("", normed, count=1)
    normed = _EMPTY_VALUE_SUFFIX_RE.sub("", normed.rstrip()).rstrip(":").strip()
    return _ENUM_PREFIX_RE.sub("", normed, count=1).strip()


def _is_substantial_key(key: str) -> bool:
    """Long enough to match a longer title by prefix/suffix without risking
    a short word matching everything (#814)."""
    return len(key) >= MIN_HEADER_KEY_CHARS or len(key.split()) >= MIN_HEADER_KEY_WORDS


def _key_matches_title(key: str, titles: set[str]) -> bool:
    """Is `key` (already past the exact-match check) a prefix or suffix of
    some KNOWN stage-1a title, after the short-candidate guard (#814) and the
    length-ratio guard (round-2 F2)?

    `titles` must be stage-1a titles only (`known`), never stage-2 entry
    hierarchy paths (`paths`): a path repeats whatever stage-1a title its
    entries were filed under, so widening the match surface to `paths` adds
    no title this function doesn't already see via `known` and only grows
    the odds of a coincidental, unrelated match -- exact matching (the
    caller's `normed in titles` check) is where `paths` still belongs, since
    an entry filed under a header IS that header, verbatim.

    Covers a header wrapped over two source lines: one physical line is a
    genuine prefix of the joined title on its own ('SERVICE ON NATIONAL GRANT
    REVIEW PANELS, STUDY SECTIONS,' is a prefix of '... SECTIONS, COMMITTEES',
    ratio 0.85) even before the two lines are joined below."""
    if not _is_substantial_key(key):
        return False
    for title in titles:
        if len(key) < len(title) * MIN_KEY_TO_TITLE_RATIO:
            continue
        if title.startswith(key) or title.endswith(key):
            return True
    return False


def _is_near_known_title(key: str, titles: set[str]) -> bool:
    """Is `key`, a header wrapped over two source lines and joined, a near
    copy (`WRAPPED_HEADER_MIN_SIMILARITY`) of some KNOWN stage-1a title?
    Same short-key guard as `_key_matches_title`; the cheap length and
    character-bag ratios run first, so the full ratio is computed only for a
    title of about the same length and letters (#1232)."""
    if not _is_substantial_key(key):
        return False
    matcher = SequenceMatcher(None)
    matcher.set_seq2(key)  # the sequence difflib indexes: once, not per title
    for title in titles:
        matcher.set_seq1(title)
        if (matcher.real_quick_ratio() >= WRAPPED_HEADER_MIN_SIMILARITY
                and matcher.quick_ratio() >= WRAPPED_HEADER_MIN_SIMILARITY
                and matcher.ratio() >= WRAPPED_HEADER_MIN_SIMILARITY):
            return True
    return False


def _name_words(text: object) -> list[str]:
    return re.findall(r"[^\W\d_]+", str(text or "").lower())


def _owner_name_words(stage4: dict | None) -> tuple[set[str], set[str]]:
    """(every word of the stage-4 ``cv_owner`` name, the first+last words a
    match must carry). Both empty unless the block has a first AND last name."""
    owner = (stage4 or {}).get("cv_owner") or {}
    first = _name_words(owner.get("first_name"))
    last = _name_words(owner.get("last_name"))
    if not (first and last):
        return set(), set()
    allowed = {*first, *last, *_name_words(owner.get("middle_name")),
               *_name_words(owner.get("full_name"))}
    return allowed, {first[0], last[-1]}


def _is_owner_name(cand: str, allowed: set[str], required: set[str]) -> bool:
    """Is this candidate the CV owner's own name (document furniture, #539)?
    Subset by construction: a line is exempt only when every word is an
    owner-name word or a single-letter initial AND it carries the owner's
    first and last name; anything else keeps the baseline verdict."""
    words = _name_words(cand)
    return bool(required) and required <= set(words) and all(
        w in allowed or len(w) == 1 for w in words)


def _contact_block_keys(stage4: dict | None) -> set[str]:
    """`_header_key`s of every line and tab cell of the stage-4 entries coded
    Personal Data: the contact block a CV opens with -- its title line
    ("CURRICULUM VITAE, <name>"), the employer, the address. Stage 3b filed
    the line as the owner's personal data, so it is not a section header
    that segmentation missed (EBYSBC: HFAJCC's employer line and MIFYLG's
    title line, both verified false; no verified true positive is such a
    line)."""
    return {_header_key(part)
            for entry in (stage4 or {}).get("entries", [])
            if entry.get("taxonomy_code") == PERSONAL_DATA_CODE
            for part in re.split(r"[\n\t]", str(entry.get("text") or ""))}


def lint_missed_headers(candidates: list[str], stage1a: dict,
                        stage2: dict, stage4: dict | None = None) -> list[dict]:
    """Header-looking source lines absent from the 1a hierarchy AND from
    every entry hierarchy path: a header demoted to content misroutes
    everything filed under it. With the optional stage 4, the CV owner's own
    name (``cv_owner``, #539) and any other line of the Personal Data block
    (`_contact_block_keys`) are document furniture, not headers. A candidate
    that is not ALL-CAPS reached the list through its Heading style alone and
    never sets the run's severity (`WEAK_CANDIDATE_SEVERITY`, #1232)."""
    allowed, required = _owner_name_words(stage4)
    contact_block = _contact_block_keys(stage4)
    known = {_header_key(t) for t in _hierarchy_titles(stage1a)}
    paths = {_header_key(h) for e in stage2.get("entries", [])
             for h in (e.get("hierarchy") or [])}
    titles = known | paths

    findings, strong, seen = [], [], set()
    n = len(candidates)
    skip_next = False
    for i, cand in enumerate(candidates):
        if skip_next:
            skip_next = False
            continue
        normed = _header_key(cand)
        if not normed or normed in seen:
            continue

        # Wrapped header (#814): the two physical source lines join into one
        # hierarchy node ('SERVICE ON ... SECTIONS,' + 'COMMITTEES:'), and the
        # second half alone is too short to pass the prefix/suffix guard
        # above -- 'committees' never becomes a header on its own. Check the
        # join BEFORE the single-line checks below, so this case is not
        # short-circuited by the first line alone already matching a longer
        # title by prefix.
        nxt = candidates[i + 1] if i + 1 < n else None
        joined = _header_key(f"{cand} {nxt}") if nxt is not None else ""
        if joined and (joined in titles or _key_matches_title(joined, known)
                       or _is_near_known_title(joined, known)):
            seen.add(normed)
            seen.add(_header_key(nxt))
            skip_next = True
            continue

        seen.add(normed)
        if normed in titles or _key_matches_title(normed, known):
            continue
        if _is_owner_name(cand, allowed, required) or normed in contact_block:
            continue
        finding = _finding(
            "missed_headers", "WARN",
            f"header-like source line missing from segmentation: '{cand}'",
            [cand])
        findings.append(finding)
        if cand == cand.upper():
            strong.append(finding)
    # Severity is a property of the RUN, not of each header: one stray
    # header-like line is normal, a dozen means segmentation lost the document's
    # shape. This lint emits one finding per header, so without this the finding
    # count doubled as the severity and a long CV always looked worse (#438).
    # Only ALL-CAPS candidates are counted and take that severity; a Heading-
    # styled line that is not ALL-CAPS stays at WEAK_CANDIDATE_SEVERITY (#1232).
    severity = _magnitude_severity(len(strong), MISSED_HEADERS_WARN_COUNT)
    for f in findings:
        f["severity"] = WEAK_CANDIDATE_SEVERITY
    for f in strong:
        f["severity"] = severity
    return findings


# --------------------------------------------------------------------------
# Stage 1b placed almost none of 1a's headings.
#
# DPEHSZ (EBYSBC E17, DPEHSZ-04): the converted input kept only bare ':'
# paragraphs where its headings stood. Stage 1a still returned 24 headings,
# stage 1b placed none of them, and every one of the 292 stage-2 entries was
# filed under the one synthetic PERSONAL DATA section, at 100% coverage. Stage
# 3b then classified the whole CV with no section context, and the run scored
# 99 with no finding. On the other 62 runs of the farm the lowest placed share
# is 70% and the largest synthetic share 14%, so either threshold below sits
# far from every healthy run.

#: Below this share of stage 1a's (non-synthetic) headings placed on a source
#: line by stage 1b, the run's sections are not the source's.
SEGMENTATION_COLLAPSE_MIN_PLACED_SHARE = 0.5

#: Above this share of stage-2 content entries inside synthetic sections, the
#: synthetic preamble holds most of the CV.
SEGMENTATION_COLLAPSE_MAX_SYNTHETIC_SHARE = 0.5

#: Stage 2's element type for a separator row, which is not an entry.
STAGE2_BREAK_TYPE = "break"
#: Stage 2's element type for a heading line.
STAGE2_HEADER_TYPE = "header"


def _hierarchy_nodes(nodes: list[dict] | None) -> list[dict]:
    flat: list[dict] = []
    for node in nodes or []:
        flat.append(node)
        flat.extend(_hierarchy_nodes(node.get("children")))
    return flat


def _placed_headings(stage1b: dict) -> tuple[int, int]:
    """(headings stage 1b placed on a source line, headings it was given),
    counting only the headings stage 1a read from the source, not the
    synthetic ones it added."""
    headings = [node for node in _hierarchy_nodes(stage1b.get("hierarchy_with_indices"))
                if not node.get("synthetic")]
    placed = sum(1 for node in headings if node.get("element_idx") is not None)
    return placed, len(headings)


class _SyntheticShare(NamedTuple):
    """How much of stage 2 sits in stage 1b's synthetic sections."""
    in_synthetic: int
    entries: int
    biggest: dict | None
    biggest_held: int


def _synthetic_entries(stage1b: dict, stage2: dict) -> _SyntheticShare:
    """Stage-2 content entries inside a synthetic section, all content
    entries, and the synthetic section holding the most of them."""
    synthetic = [section for section in stage1b.get("section_boundaries", [])
                 if section.get("synthetic")]
    entries = [entry for entry in stage2.get("entries", [])
               if entry.get("element_type") != STAGE2_BREAK_TYPE]
    held: dict[int, int] = {}
    for entry in entries:
        start = entry.get("element_idx_start")
        for i, section in enumerate(synthetic):
            if (isinstance(start, (int, float))
                    and section.get("element_idx_start", 0) <= start <= section.get("element_idx_end", -1)):
                held[i] = held.get(i, 0) + 1
                break
    if not held:
        return _SyntheticShare(0, len(entries), None, 0)
    biggest = max(held, key=held.__getitem__)
    return _SyntheticShare(sum(held.values()), len(entries), synthetic[biggest], held[biggest])


def lint_segmentation_collapse(stage1b: dict, stage2: dict) -> list[dict]:
    """Stage 1b placed under half of stage 1a's headings, or the synthetic
    preamble section holds most of stage 2's entries: the CV was classified
    without its own section structure (EBYSBC E17, DPEHSZ). One WARN per run."""
    placed, headings = _placed_headings(stage1b)
    share = _synthetic_entries(stage1b, stage2)
    few_placed = bool(headings) and placed / headings < SEGMENTATION_COLLAPSE_MIN_PLACED_SHARE
    synthetic_holds_most = (bool(share.entries) and share.in_synthetic / share.entries
                            > SEGMENTATION_COLLAPSE_MAX_SYNTHETIC_SHARE)
    if not (few_placed or synthetic_holds_most):
        return []
    evidence = [f"stage 1b placed {placed} of {headings} stage 1a headings"]
    if share.biggest is not None:
        section = share.biggest
        title = " > ".join(str(h) for h in section.get("hierarchy") or [])
        evidence.append(
            f"element_idx_start {section.get('element_idx_start')}: synthetic section "
            f"'{title}' (to {section.get('element_idx_end')}) holds {share.biggest_held} of "
            f"{share.entries} stage-2 entries")
    return [_finding(
        "segmentation_collapse", "WARN",
        f"section structure lost: {placed} of {headings} headings placed, "
        f"{share.in_synthetic} of {share.entries} entries in synthetic sections",
        evidence)]


# --------------------------------------------------------------------------
# Stage 2's own accounting: elements it never assigned to any entry (#1693).

#: Index ranges an `stage2_unaccounted` finding quotes.
UNACCOUNTED_EVIDENCE_RANGES = 5


def _index_ranges(indices: list[int]) -> list[tuple[int, int]]:
    """Sorted, de-duplicated indices folded into (first, last) runs."""
    ranges: list[tuple[int, int]] = []
    for index in sorted(set(indices)):
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], index)
        else:
            ranges.append((index, index))
    return ranges


def lint_stage2_unaccounted(stage2: dict) -> list[dict]:
    """Source elements stage 2 records in `coverage.unaccounted_indices`:
    elements no entry, header or break covers, so stage 3b never saw them
    (#1693, NECRGC TGSQIR-01: a parent section's body, 50 elements, all 24
    journal articles, while stage 1b reported 100%). Stage 2 states this
    itself, so the finding needs neither the source docx nor the render:
    10 farm runs whose last batch never came back carry it with no source
    on hand for `segmentation` to read. One WARN per run.

    A break counts as accounted: a line stage 2 labelled `break` is never
    in this finding, whatever text it holds. `lint_stage2_text_break`
    reads those."""
    coverage = stage2.get("coverage")
    raw = coverage.get("unaccounted_indices") if isinstance(coverage, dict) else None
    indices = [i for i in raw or [] if isinstance(i, int) and not isinstance(i, bool)]
    if not indices:
        return []
    ranges = _index_ranges(indices)
    evidence = [f"element_idx_start {first}: elements {first}-{last} unaccounted"
                for first, last in ranges[:UNACCOUNTED_EVIDENCE_RANGES]]
    return [_finding(
        "stage2_unaccounted", "WARN",
        f"stage 2 left {len(set(indices))} source element(s) in no entry, header or break "
        f"({len(ranges)} span(s)): their text never reached classification; a break "
        f"counts as accounted (#1693)",
        evidence)]


# --------------------------------------------------------------------------
# A list stage 2 grouped one record off (#1226).
#
# Every word survives, so no coverage check sees it: each entry holds the
# tail of one record and the head of the next. The trace it leaves is where
# each entry's date sits. Records of one list put the date in one place; a
# list cut one record off swaps it, first-to-last or last-to-first, and the
# swap starts or ends at the fragment the cut left behind: a date with no
# record (GNONHA 505, a stage-2 batch that ended on the date paragraph of
# the next record), or a record with no date (SPIHVQ 137, ZASOTV 567: a
# mentee row whose date and thesis title opened the next entry).

#: Stage-2 element types that hold record text. A header ends a list; a
#: break (`STAGE2_BREAK_TYPE`) sits between records and does not.
STAGE2_RECORD_TYPES = frozenset({"paragraph", "table_row", "table"})

#: How stage 2 joins a record's fields: one per tab or line.
_RECORD_FIELD_SPLIT_RE = re.compile(r"[\t\n]+")

#: A slash date with a two- or four-digit year ("7/1/09", "07/90", "4/30/13"),
#: which `_is_date_only_text` does not read because it needs a four-digit
#: year (NJHLUR's grant periods, CBNXND's "07/90 - present").
_SLASH_DATE = r"\d{1,2}/(?:\d{1,2}/)?(?:\d{4}|\d{2})"
_SLASH_DATE_ONLY_RE = re.compile(
    rf"\(?{_SLASH_DATE}(?:\s*[-\u2010-\u2015]\s*(?:{_SLASH_DATE}|present|current))?\)?[.,;]?",
    re.IGNORECASE)

#: Entries on each side of the fragment that must agree on where their date
#: sits, and disagree with the other side. One entry a side fired on a
#: record split over two paragraphs beside whole ones (5 farm runs of one CV).
DATE_SHIFT_SIDE_ENTRIES = 2

#: A dateless, digit-free, one-field line of at most this many words between
#: two lists is a sub-heading typed as a paragraph ("Educational
#: Background"), not a record's fragment: the lists either side of it are two
#: tables written two ways.
DATE_SHIFT_HEADING_MAX_WORDS = 5

#: Characters of an entry's text a stage-2 finding quotes.
STAGE2_EVIDENCE_TEXT_CHARS = 100


def _record_fields(text: str) -> list[str]:
    return [field.strip() for field in _RECORD_FIELD_SPLIT_RE.split(text) if field.strip()]


def _is_date_field(field: str) -> bool:
    return _is_date_only_text(field) or bool(_SLASH_DATE_ONLY_RE.fullmatch(field))


class DatePlace(NamedTuple):
    """Where an entry's date fields sit among its fields."""
    dated: bool      # some field is a date
    date_only: bool  # every field is a date
    leads: bool      # the first field is a date
    trails: bool     # the last field is a date

    @property
    def fragment(self) -> bool:
        """A date with no record, or a record with no date."""
        return self.date_only or not self.dated


def _date_place(text: str) -> DatePlace:
    dates = [_is_date_field(field) for field in _record_fields(text)]
    return DatePlace(any(dates), bool(dates) and all(dates),
                     bool(dates) and dates[0], bool(dates) and dates[-1])


def _record_lists(stage2: dict) -> list[list[dict]]:
    """Runs of consecutive stage-2 record entries under one hierarchy path;
    a header, or an entry under another path, starts a new run."""
    lists: list[list[dict]] = []
    current: list[dict] = []
    path: tuple[str, ...] | None = None
    for entry in stage2.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("element_type") == STAGE2_BREAK_TYPE:
            continue
        is_record = (entry.get("element_type") in STAGE2_RECORD_TYPES
                     and isinstance(entry.get("text"), str))
        entry_path = tuple(str(level) for level in entry.get("hierarchy") or [])
        if not is_record or entry_path != path:
            if current:
                lists.append(current)
            current = []
            path = entry_path if is_record else None
        if is_record:
            current.append(entry)
    if current:
        lists.append(current)
    return lists


def _is_sub_heading(text: str) -> bool:
    fields = _record_fields(text)
    return (len(fields) == 1 and not re.search(r"\d", fields[0])
            and len(fields[0].split()) <= DATE_SHIFT_HEADING_MAX_WORDS)


def _side_order(places: list[DatePlace]) -> tuple[bool, bool] | None:
    """The (leads, trails) every entry of one side shares, or None when the
    side is short, holds a fragment, or disagrees with itself."""
    if len(places) < DATE_SHIFT_SIDE_ENTRIES or any(place.fragment for place in places):
        return None
    orders = {(place.leads, place.trails) for place in places}
    return orders.pop() if len(orders) == 1 else None


def _date_shift_junctions(entries: list[dict]) -> list[tuple[int, tuple[bool, bool], tuple[bool, bool]]]:
    """(position, order before, order after) for each fragment in one list
    whose neighbours put their dates in two different places."""
    places = [_date_place(entry["text"]) for entry in entries]
    junctions = []
    for i, place in enumerate(places):
        if not place.fragment or (not place.dated and _is_sub_heading(entries[i]["text"])):
            continue
        before = _side_order(places[max(0, i - DATE_SHIFT_SIDE_ENTRIES):i])
        after = _side_order(places[i + 1:i + 1 + DATE_SHIFT_SIDE_ENTRIES])
        if before is not None and after is not None and before != after:
            junctions.append((i, before, after))
    return junctions


def _order_words(order: tuple[bool, bool]) -> str:
    leads, trails = order
    if leads and trails:
        return "first and last"
    if leads:
        return "first"
    return "last" if trails else "mid-record"


def lint_record_date_shift(stage2: dict) -> list[dict]:
    """A list stage 2 grouped one record off (#1226): a date-only or dateless
    entry whose two neighbours before it put their date in one place and the
    two after it in another (first field, last field, or inside). Each
    shifted entry then renders with the next or previous record's date,
    place or title (NECRGC: 9 of the 13 verified HIGHs of this class;
    GNONHA, NJHLUR, SPIHVQ, XUTTKJ, YIXQFB, WBZIDP, ZASOTV). WARN, one per
    fragment. Reads stage 2 only.

    Not judged: a list shifted from its first entry to its last, where no
    entry differs from its neighbours (CBNXND's stacked date column), and a
    date line paired with the wrong neighbour inside an otherwise undated
    list (QZCHAG 18, 133)."""
    findings = []
    for entries in _record_lists(stage2):
        for i, before, after in _date_shift_junctions(entries):
            fragment, following = entries[i], entries[i + 1]
            kind = "a date with no record" if _date_place(fragment["text"]).dated else "a record with no date"
            title = " > ".join(str(level) for level in fragment.get("hierarchy") or [])
            findings.append(_finding(
                "record_date_shift", "WARN",
                f"entry {fragment.get('element_idx_start')}: list '{title}' grouped one record "
                f"off -- this entry is {kind}, the entries before it carry their date "
                f"{_order_words(before)}, the entries after it {_order_words(after)} (#1226)",
                [fragment["text"][:STAGE2_EVIDENCE_TEXT_CHARS],
                 f"entry {following.get('element_idx_start')}: "
                 f"{following['text'][:STAGE2_EVIDENCE_TEXT_CHARS]}"]))
    return findings


# --------------------------------------------------------------------------
# A heading line that also holds the section's first record (#1254, #1252).
#
# Stage 1b maps "LABEL: first record" to a heading, and stage 2 types the
# whole line `header`, so the record after the label never reaches stage 3b
# (WBBUHK 29/32/36/49: the only board certification, the first licence and
# society, the current employer; MFJRKW 52: an award). The heading counts as
# covered, so `source_coverage_pct` reads 100.

#: Text after the heading words that is a note on the list, not a record,
#: though it has two words: a parenthetical or bracketed aside ("(past 3
#: years only)", "[number withheld]"), "not applicable", or a page number
#: ("Page 2 of 5"). A one-word note ("none", "(cont'd)") already falls under
#: HEADER_RECORD_MIN_WORDS.
_HEADER_NOTE_RE = re.compile(
    r"^(?:\(.*\)|\[.*\]|not applicable|page\s*\d+(?:\s*of\s*\d+)?)[.:]?$",
    re.IGNORECASE)
#: Separators between a run-in heading and its first record.
_HEADER_SEPARATORS = " \t:;,.-\u2013\u2014"
#: The fewest letter words a remainder needs to be a record rather than a
#: stray word ("program", "Where?").
HEADER_RECORD_MIN_WORDS = 2
_LETTER_WORD_RE = re.compile(r"[^\W\d_]{2,}")


def _header_remainder(entry: dict) -> str | None:
    """The text a header entry holds after its own heading words, when it
    reads as a record: two or more letter words, not a note, not ALL CAPS
    (a column-header row, "YEAR JOINED")."""
    text, path = entry.get("text"), entry.get("hierarchy") or []
    if not isinstance(text, str) or not path:
        return None
    flat, heading = " ".join(text.split()), " ".join(str(path[-1]).split())
    if not heading or not flat.lower().startswith(heading.lower()):
        return None
    rest = flat[len(heading):].strip(_HEADER_SEPARATORS)
    if (len(_LETTER_WORD_RE.findall(rest)) < HEADER_RECORD_MIN_WORDS
            or _HEADER_NOTE_RE.match(rest) or rest.isupper()):
        return None
    return rest


def lint_header_consumed_record(stage2: dict,
                                blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """A stage-2 `header` entry whose text is its own heading followed by a
    record (#1254, #1252): the record is never classified, so it renders
    nowhere. With the rendered document, a remainder that does render
    somewhere is left alone (a heading that merely starts like a record).
    WARN, one per entry. Reads stage 2; the docx when present."""
    rendered = _haystacks(blocks).text if blocks is not None else None
    findings = []
    for entry in stage2.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("element_type") != STAGE2_HEADER_TYPE:
            continue
        rest = _header_remainder(entry)
        if rest is None or (rendered is not None and squash(rest) in rendered):
            continue
        findings.append(_finding(
            "header_consumed_record", "WARN",
            f"entry {entry.get('element_idx_start')}: heading '{entry['hierarchy'][-1]}' also "
            f"holds a record, which stage 2 kept as part of the heading: it is not "
            f"classified or rendered (#1254)",
            [rest[:STAGE2_EVIDENCE_TEXT_CHARS]]))
    return findings


# --------------------------------------------------------------------------
# A break that holds a record line (#1226 forensics, G4).
#
# Stage 2 labels a line `break` when the model's reply claims it for no
# entry. `stage2_unaccounted` counts a break as accounted, so nothing reads
# its text: on the 83 stored NECRGC/b13/s3 runs, 11 hold whole record lines
# there (employment histories, appointments, talks, a licence, a patent).
# A break is read the way `source_line_coverage` reads a source line, with
# its gate: text that gate would judge (six or more words, not boilerplate,
# a heading, a name list or withheld data) and that no rendered line holds
# is text the document has lost. Stage 6 puts most of it in the Appendix,
# so with the render the finding is rare.

#: INFO: on the 57 runs with a current render, the only 3 breaks whose text
#: renders nowhere are a running footer, a sub-heading and a column header
#: (0 of 3 true; doctor/PRECISION.md, S2-TB), under the 50% a WARN needs.
TEXT_BREAK_SEVERITY = "INFO"
#: Breaks a `stage2_text_break` finding quotes, per section.
TEXT_BREAK_EVIDENCE_LINES = 3


def _element_span(entry: dict) -> range:
    """The element indices an entry covers; empty when its bounds are not
    integers (an int-valued string is read as one, as stage 2 wrote them on
    older runs)."""
    try:
        return range(int(entry["element_idx_start"]), int(entry["element_idx_end"]) + 1)
    except (KeyError, TypeError, ValueError):
        return range(0)


def _set_aside_text(entry: dict, headings: frozenset[str], output: _Output) -> str | None:
    """A break entry's text, its lines joined, when `source_line_coverage`'s
    gate would judge it and finds some of it under COVERAGE_MIN_SHARE
    rendered; None otherwise. Each printed line is judged as that lint
    judges it. Only when it judges none are the lines judged joined: a
    record a soft return splits in two ("<title>" / "<college>, <city> --
    <dates>") has under COVERAGE_MIN_PAIRS pairs on each line (12 record
    lines on the 83 stored runs, and no other hit). Joining is the fallback,
    not the rule: over 23 joined lines the personal-data matcher reads a
    span across lines and drops the whole break."""
    lines = [line.text for line in _printed_lines([str(entry.get("text") or "")])]
    shares = [share for share in (_rendered_share(line, "", headings, output) for line in lines)
              if share is not None]
    joined = " ".join(" ".join(lines).split())
    if not shares:
        share = _rendered_share(joined, "", headings, output)
        shares = [share] if share is not None else []
    return joined if shares and min(shares) < COVERAGE_MIN_SHARE else None


def lint_stage2_text_break(stage2: dict, stage1a: dict | None = None,
                           blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """Stage-2 `break` entries holding text `source_line_coverage` would
    judge (#1226 forensics G4): the model's reply claimed the line for no
    entry, so it is never classified, and `stage2_unaccounted` counts it as
    accounted. A break whose elements another entry also covers is left
    alone, as is one whose bounds are unreadable: older stage-2 runs wrote a
    break beside the entry holding the same line (2,940 of 14,721 text
    breaks over the 249 runs measured). One finding per section, led by the
    first such break's index. With the rendered document, a line that
    renders somewhere (stage 6 puts many in the Appendix) is left alone;
    stage 1a's headings are never judged."""
    output = _Output(blocks or [])
    headings = _heading_texts(stage1a)
    breaks: list[dict] = []
    claimed: set[int] = set()
    for entry in stage2.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("element_type") == STAGE2_BREAK_TYPE:
            breaks.append(entry)
        else:
            claimed.update(_element_span(entry))
    by_section: dict[tuple[str, ...], list[tuple[dict, str]]] = {}
    for entry in breaks:
        if set(_element_span(entry)) <= claimed:
            continue
        text = _set_aside_text(entry, headings, output)
        if text is not None:
            path = tuple(str(level) for level in entry.get("hierarchy") or [])
            by_section.setdefault(path, []).append((entry, text))
    findings = []
    for path, hits in by_section.items():
        findings.append(_finding(
            "stage2_text_break", TEXT_BREAK_SEVERITY,
            f"entry {hits[0][0].get('element_idx_start')}: section '{' > '.join(path)}': "
            f"{len(hits)} stage-2 break(s) hold text no entry claimed, so it was never "
            f"classified{'; it renders nowhere' if blocks is not None else ''} (#1226)",
            [f"entry {entry.get('element_idx_start')}: {text[:STAGE2_EVIDENCE_TEXT_CHARS]}"
             for entry, text in hits[:TEXT_BREAK_EVIDENCE_LINES]]))
    return findings


# --------------------------------------------------------------------------
# Two entries that share source lines (#1433).
#
# Each record entry should hold its own lines. When two hold the same ones,
# the shared lines can render twice, often once under the wrong section
# (ATUVAL 867-869 under HONORS and under a committee heading; web228 1695
# under Books and under Non-refereed Publications), and the dates can pair
# with the wrong records (ATUVAL 598/600/602, sliding windows over a
# flattened table). Stage 2 merges overlapping paragraph windows inside one
# section since #1798; this reads what is left: spans across two sections,
# and table-row spans (web172 112.1-112.13 and 112.3-112.33).

#: The fewest source elements two entries with the SAME span must share to be
#: reported, so two one-element entries on one element are not judged: on the
#: 324 distinct local stage-2 artifacts measured for #1433 there are 6,886
#: such pairs on 21 of them, one single-element entry that older stage-2 runs
#: emitted twice (filter_extraction_noise drops that copy). Entries whose spans
#: differ are reported on one shared element when their texts share a line
#: (ATUVAL 863-867 under HONORS, 867-869 under a committee heading): 306 such
#: pairs on 28 artifacts, 305 of them sharing text.
SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS = 2
#: Overlapping pairs a `stage2_span_overlap` finding quotes, per section pair.
SPAN_OVERLAP_EVIDENCE_PAIRS = 3


def _row_span(entry: dict) -> frozenset[str]:
    """The table-row keys ("112.3") a table-row entry covers, from its start
    row to its end row; empty, so the entry is not compared, when the two are
    not rows of one table: the rows between them cannot be listed from stage
    2's own record."""
    start, end = entry.get("element_idx_start"), entry.get("element_idx_end")
    if not (isinstance(start, str) and isinstance(end, str)):
        return frozenset()
    parent, _, first = start.partition(".")
    end_parent, _, last = end.partition(".")
    if parent != end_parent or not (first.isdigit() and last.isdigit()):
        return frozenset()
    return frozenset(f"{parent}.{row}" for row in range(int(first), int(last) + 1))


def _span_keys(entry: dict) -> frozenset[int | str]:
    """The source elements a record entry covers: paragraph indices, or
    table-row keys when its bounds are row keys. The two never compare
    equal, so a whole-table entry is not matched against its own rows."""
    return frozenset(_element_span(entry)) or _row_span(entry)


#: What a stage-2 entry's text is split on to find a line, cell or element it
#: shares with another: a tab, a line break, and the " | " between table cells.
SPAN_OVERLAP_TEXT_PARTS = re.compile(r"\t|\n| \| ")


def _shares_text(text: str, other: str) -> bool:
    """True when a line, cell or element of one text is inside the other one,
    compared normalized. A span alone is not enough: web30 1012.0-1012.1 and
    1012.1 share row 1 by span, but the first holds only row 0's text."""
    texts = (norm(text), norm(other))
    return any(part and part in texts[1 - side]
               for side, raw in enumerate((text, other))
               for part in map(norm, SPAN_OVERLAP_TEXT_PARTS.split(raw)))


def _is_reported_overlap(entry: dict, earlier: dict, span: frozenset[int | str],
                         earlier_span: frozenset[int | str], shared: int) -> bool:
    """Two entries whose spans share ``shared`` (at least one) source elements
    are reported when they share SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS or more.
    Below that, they are reported only when the spans differ (the same span is
    one element emitted twice) and the two texts share a line (`_shares_text`)."""
    if shared >= SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS:
        return True
    return span != earlier_span and _shares_text(str(entry.get("text") or ""),
                                                 str(earlier.get("text") or ""))


def _overlapping_pairs(stage2: dict) -> list[tuple[dict, dict, int]]:
    """(earlier entry, later entry, elements shared) for each record entry
    whose span overlaps an entry before it (`_is_reported_overlap`). The
    first entry it shares SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS or more elements
    with is named, else the first it shares one with."""
    entries = [entry for entry in stage2.get("entries") or []
               if isinstance(entry, dict) and entry.get("element_type") in STAGE2_RECORD_TYPES]
    spans = [_span_keys(entry) for entry in entries]
    owners: dict[int | str, list[int]] = {}
    pairs = []
    for later, entry in enumerate(entries):
        span = spans[later]
        shared = Counter(earlier for key in span for earlier in owners.get(key, ()))
        reported = [i for i, count in shared.items()
                    if _is_reported_overlap(entry, entries[i], span, spans[i], count)]
        earlier = min(reported, key=lambda i: (shared[i] < SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS, i), default=None)
        if earlier is not None:
            pairs.append((entries[earlier], entry, shared[earlier]))
        for key in span:
            owners.setdefault(key, []).append(later)
    return pairs


def _section_title(entry: dict) -> str:
    return " > ".join(str(level) for level in entry.get("hierarchy") or [])


def lint_stage2_span_overlap(stage2: dict) -> list[dict]:
    """Record entries that share source elements with an entry before them
    (#1433), one element emitted twice excepted (`_is_reported_overlap`): the
    shared lines can render twice, once under the wrong section when the two
    sit in different ones. WARN, one per pair of sections, led by the first
    such entry that shares SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS or more, else
    the first. Reads stage 2 only."""
    by_sections: dict[tuple[str, str], list[tuple[dict, dict, int]]] = {}
    for earlier, later, shared in _overlapping_pairs(stage2):
        by_sections.setdefault((_section_title(earlier), _section_title(later)), []).append(
            (earlier, later, shared))
    findings = []
    for (earlier_title, later_title), pairs in by_sections.items():
        # Multi-element pairs lead and fill the evidence first (stable sort), so a
        # finding the two-element rule already gave keeps its lead entry and lines.
        pairs.sort(key=lambda pair: pair[2] < SPAN_OVERLAP_MIN_IDENTICAL_ELEMENTS)
        where = (f"section '{later_title}'" if earlier_title == later_title
                 else f"sections '{earlier_title}' and '{later_title}'")
        findings.append(_finding(
            "stage2_span_overlap", "WARN",
            f"entry {pairs[0][1].get('element_idx_start')}: {where}: {len(pairs)} stage-2 "
            f"entr{'y shares' if len(pairs) == 1 else 'ies share'} source lines with an earlier "
            f"entry, so those lines can render twice (#1433)",
            [f"entry {later.get('element_idx_start')}-{later.get('element_idx_end')} shares "
             f"{shared} element(s) with entry {earlier.get('element_idx_start')}-"
             f"{earlier.get('element_idx_end')}: {str(later.get('text') or '')[:STAGE2_EVIDENCE_TEXT_CHARS]}"
             for earlier, later, shared in pairs[:SPAN_OVERLAP_EVIDENCE_PAIRS]]))
    return findings
