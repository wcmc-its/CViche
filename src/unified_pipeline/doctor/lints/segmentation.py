"""Lints for how the source document was cut into a hierarchy (#493).

One responsibility: whether stage 1a/2 preserved the SHAPE of the source --
every line accounted for, entries the size of a record rather than a chapter,
and every header-looking line actually promoted to a hierarchy node.

Both lints read only the source lines and the stage 1a/2 artifacts. Nothing
here looks at the rendered document: a header demoted to content misroutes
everything filed under it long before stage 6 runs, and the finding is about
the cut, not the page.

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
from difflib import SequenceMatcher
from typing import NamedTuple

from unified_pipeline.core.text_norm import fold_quotes, norm
from unified_pipeline.segmentation_regression import (
    compute_metrics,
    find_lost_blocks,
    lint_metrics,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE

from ..shared import _finding, _magnitude_severity

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
