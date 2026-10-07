"""Gold-set segmentation regression harness (precondition for #208/#212).

Segmentation changes (stage 1a hierarchy detection, stage 2 entry
extraction) affect EVERY CV, and their failures are silent: content doesn't
error out, it just vanishes into mega-entries, the appendix, or thin air
(run 89HQVQ lost 7 of 8 grants this way). This harness makes those failures
measurable before a segmentation PR lands.

It works on STRUCTURAL metrics, not exact text diffs, because stages 1a/2a
call an LLM and are not run-to-run deterministic. Metrics per CV:

- text coverage: % of substantive source lines (paragraphs AND table-cell
  paragraphs — the 89HQVQ lesson) whose text survives into some entry,
  plus the list of lost lines
- entry counts (content/header/break), empty content entries, duplicates
- mega-entries (one entry holding several record-like lines: the
  layout-table collapse smell) and max entry size
- headers detected in the 1a hierarchy (count + titles, so a compare shows
  exactly which headers appeared/disappeared)

Workflow for a segmentation PR:

    # on dev (baseline):
    PYTHONPATH=src python -m unified_pipeline.segmentation_regression snapshot baseline
    # on the PR branch (candidate):
    PYTHONPATH=src python -m unified_pipeline.segmentation_regression snapshot candidate
    PYTHONPATH=src python -m unified_pipeline.segmentation_regression compare baseline candidate

`compare` exits non-zero on regression. `lint <label>` flags absolute
problems in one snapshot without a baseline. Snapshots cost real LLM money
(~$0.25/CV, ~$2.50 for the 10-CV gold corpus) — `--uids` runs a subset.

The gold corpus lives in outputs/gold_set/<stamp>/originals/ (local only,
gitignored, PII — see its README). Snapshots are written next to it under
outputs/gold_set/segsnap_<label>/ and are likewise never committed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

from unified_pipeline.core.template_boilerplate import (
    is_foreign_template_instruction,
    is_near_template_instruction,
    is_template_instruction,
)
from unified_pipeline.core.text_norm import (
    SUBSTANTIVE_LINE_CHARS,
    looks_like_record,
    norm,
    squash,
)
from unified_pipeline.stage6.normalization.pii import redact_pre_llm_values

# Aliases for callers that predate the move to core.text_norm.
_norm = norm
_squash = squash
_looks_like_record = looks_like_record

logger = logging.getLogger(__name__)

# A content entry counts as a mega-entry when it packs this many
# record-like lines (the layout-table collapse smell, #208).
MEGA_ENTRY_MIN_RECORDS = 3

# Coverage may wobble slightly between runs of IDENTICAL code because the
# delimiter LLM is not deterministic; only a drop beyond this is a regression.
COVERAGE_DROP_TOLERANCE_PTS = 1.0


# ------------------------------------------------------------- typed structures
# The stage 1a/2 producers are not themselves typed (they call LLMs and build
# plain dicts), so these TypedDicts document the shapes this harness reads and
# let mypy catch a key/type drift here. total=False on the stage payloads: every
# field is accessed defensively via .get(), mirroring the untyped producers.

class HierarchyNode(TypedDict, total=False):
    """A stage-1a hierarchy node; ``children`` nests the same shape."""
    text: str
    children: list[HierarchyNode]


class Stage1A(TypedDict, total=False):
    """Stage-1a segmentation output (hierarchy detection)."""
    document_uid: str
    hierarchy: list[HierarchyNode]
    meta: dict[str, Any]


class Entry(TypedDict, total=False):
    """One stage-2 extracted entry."""
    element_type: str
    text: str
    element_idx_start: Any
    hierarchy: list[str]


class Stage2(TypedDict, total=False):
    """Stage-2 entry-extraction output."""
    entries: list[Entry]
    total_cost: float


class Metrics(TypedDict):
    """Structural metrics for one CV — the snapshot unit compare/lint read."""
    source_lines: int
    substantive_lines: int
    text_coverage_pct: float
    lost_lines: list[str]
    entries_total: int
    entries_content: int
    empty_content: int
    duplicate_entries: int
    mega_entries: int
    # max_entry_chars and per_h1_content_counts (below) are informational
    # only: written to metrics.json for manual inspection, but neither
    # compare_metrics() nor lint_metrics() reads them (#617). entries_total
    # and entries_content are the same shape -- LLM segmentation can
    # legitimately merge or split entries between runs without losing
    # content, so an entry-count delta alone isn't a regression signal, and
    # wiring either into comparison would need a threshold this harness does
    # not have evidence to set. Comparison stays scoped to the loss/noise
    # signals below it: coverage, lost lines, headers, and the three
    # _COUNT_KEYS.
    max_entry_chars: int
    headers_detected: int
    header_titles: list[str]
    per_h1_content_counts: dict[str, int]  # informational only -- see above


def _is_int_count(value: object) -> bool:
    # bool is an int subclass and would otherwise silently pass.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_coverage_pct(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_str_int_dict(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and _is_int_count(v) for k, v in value.items()
    )


# One (checker, description) pair per Metrics field, keyed by field name so
# this mapping and the TypedDict cannot drift apart (#T2.2) -- pinned by
# test_metrics_field_checkers_cover_every_required_key. _load_metrics() uses
# this to validate the FULL persisted shape, not just key presence: a
# corrupted or hand-edited metrics.json (a count as a string, a coverage
# value out of [0, 100], a list field holding a non-list) previously passed
# and failed later with an unrelated TypeError. The description is a plain
# string, not the checker's __doc__ -- docstrings are stripped under -OO,
# which would blank the error message (#T2.2 follow-up).
_METRICS_FIELD_CHECKERS: dict[str, tuple[Callable[[Any], bool], str]] = {
    "source_lines": (_is_int_count, "an int count"),
    "substantive_lines": (_is_int_count, "an int count"),
    "text_coverage_pct": (_is_coverage_pct, "a number in [0, 100]"),
    "lost_lines": (_is_str_list, "a list of str"),
    "entries_total": (_is_int_count, "an int count"),
    "entries_content": (_is_int_count, "an int count"),
    "empty_content": (_is_int_count, "an int count"),
    "duplicate_entries": (_is_int_count, "an int count"),
    "mega_entries": (_is_int_count, "an int count"),
    "max_entry_chars": (_is_int_count, "an int count"),
    "headers_detected": (_is_int_count, "an int count"),
    "header_titles": (_is_str_list, "a list of str"),
    "per_h1_content_counts": (_is_str_int_dict, "a dict of str to int"),
}


Verdict = Literal["REGRESSION", "IMPROVED", "OK"]

# The three count metrics that compare/lint treat uniformly. Kept as one list so
# the regression, improvement and lint passes can't drift apart.
_COUNT_KEYS = ("mega_entries", "duplicate_entries", "empty_content")


def _counts(m: Metrics) -> dict[str, int]:
    """The _COUNT_KEYS as a plain dict, so callers can iterate them by key
    (TypedDict rejects a non-literal subscript)."""
    return {
        "mega_entries": m["mega_entries"],
        "duplicate_entries": m["duplicate_entries"],
        "empty_content": m["empty_content"],
    }


# Unicode letters/digits, no underscore: identical to [a-z0-9]+ on the
# lowercased ASCII input this used to see (#541).
_TOKEN_RE = re.compile(r"[^\W_]+")


def _tokens(text: str) -> Counter[str]:
    """Word/number token MULTISET for the coverage check (see
    compute_metrics). A set would collapse repeated tokens, so a source line
    repeating an entry's words would read as covered (#T2.3)."""
    return Counter(_TOKEN_RE.findall(norm(text)))


def _token_list(text: str) -> list[str]:
    """Word/number tokens in document ORDER (`_tokens` drops the order)."""
    return _TOKEN_RE.findall(norm(text))


#: How many tokens beyond the line's own count the smallest stretch of an
#: entry holding all of the line's tokens may span for the line to still count
#: as covered by that entry (#610). Stage 2 splices whole phrases into a line
#: ('B.S.' + 'University of Utah' + '(Biology)') and can reorder its parts;
#: the same words merely scattered through an unrelated entry span far more.
COVERAGE_WINDOW_SLACK = 12


def _has_compact_window(need: Counter[str], entry_tokens: list[str], max_span: int) -> bool:
    """True when some run of at most `max_span` consecutive `entry_tokens`
    holds every token of `need` (with multiplicity), in any order."""
    have: Counter[str] = Counter()
    missing = sum(need.values())
    lo = 0
    for hi, token in enumerate(entry_tokens):
        if token in need:
            have[token] += 1
            missing -= have[token] <= need[token]
        while missing == 0:
            if hi - lo + 1 <= max_span:
                return True
            left = entry_tokens[lo]
            if left in need:
                have[left] -= 1
                missing += have[left] < need[left]
            lo += 1
    return False


# --------------------------------------------------------------- source lines

#: The block id `iter_source_block_lines` gives a body paragraph (one outside
#: every table). Body text has no container to scope it, so the block view
#: leaves it to the document-wide coverage figure.
BODY_BLOCK = -1


def iter_source_lines(docx_path: str) -> list[str]:
    """Every text line of the source document, INCLUDING paragraphs inside
    table cells (recursively): CVs routinely use 1×1 layout tables as section
    containers, and a coverage metric that can't see into cells would have
    missed the 89HQVQ grant loss entirely."""
    return [line for _, line in iter_source_block_lines(docx_path)]


def iter_source_block_lines(docx_path: str) -> list[tuple[int, str]]:
    """`iter_source_lines`, same lines in the same order, each paired with
    the table it sits in: a per-table id numbered in walk order (a nested
    table gets its own), or BODY_BLOCK. The block view (#815) scopes coverage
    to one table, so a small table lost whole is not a rounding error against
    the rest of the document."""
    from docx import Document  # local import: harness is optional tooling
    from docx.table import Table
    from unified_pipeline.core.docx_structure_extractor import get_paragraph_text

    lines: list[tuple[int, str]] = []
    table_ids = itertools.count()
    # python-docx returns the same underlying cell (_tc element) for every grid
    # position a vMerge/hMerge spans, so an unguarded walk counts a merged
    # cell's paragraphs once per spanned row/column. Mirror the identity guard
    # at core/docx_structure_extractor.py's seen_cells handling (~line 642):
    # identity is the cell's _tc element, not the _Cell wrapper (a fresh
    # wrapper is constructed on every access, so wrapper identity never
    # matches). One set shared by the top-level loop and the recursive calls
    # below covers both entry points into walk_cell (#615 item 1).
    seen_cells: set = set()

    def walk_table(tbl: Table) -> None:
        block = next(table_ids)
        for row in tbl.rows:
            for cell in row.cells:
                walk_cell(cell, block)

    def walk_cell(cell, block: int) -> None:
        if cell._tc in seen_cells:
            return
        seen_cells.add(cell._tc)
        for para in cell.paragraphs:
            text = get_paragraph_text(para, tab_char='\t')
            if text.strip():
                lines.append((block, text))
        for tbl in cell.tables:
            walk_table(tbl)

    doc = Document(docx_path)
    for para in doc.paragraphs:
        text = get_paragraph_text(para, tab_char='\t')
        if text.strip():
            lines.append((BODY_BLOCK, text))
    for tbl in doc.tables:
        walk_table(tbl)
    return lines


# ------------------------------------------------------------------- metrics

def _walk_headers(nodes: list[HierarchyNode] | None, titles: list[str]) -> None:
    for node in nodes or []:
        title = norm(node.get("text", ""))
        if title:
            titles.append(title)
        _walk_headers(node.get("children"), titles)


# Template text shorter than this still counts toward coverage: a short
# template string is also a real value in a CV. "Full-time salaried by Weill
# Cornell" (35 chars) is one of the template's Employment Status options and,
# where a CV keeps only that option, the author's answer -- dev lost it on 12
# runs. 976WPY's template prompts are 42-55 chars.
TEMPLATE_SCAFFOLDING_MIN_CHARS = 40


def _is_template_scaffolding(line: str) -> bool:
    """A source line that is WCM template instruction text, verbatim or
    another revision's rewording, or another institution's instruction
    scaffolding recognised by shape (#530, the same detector stage 2 drops
    with), and long enough not to double as a value.
    Not `is_template_label_line`: a short label ("2. Principal Investigator",
    "Weill Cornell Medical College") is also a real value in a CV, and its
    loss must still count."""
    if len(norm(line)) < TEMPLATE_SCAFFOLDING_MIN_CHARS:
        return False
    return (is_template_instruction(line) or is_near_template_instruction(line)
            or is_foreign_template_instruction(line))


def _substantive(source_lines: list[str]) -> list[str]:
    """The source lines coverage is measured over: long enough not to match
    by accident, and not WCM template scaffolding. The template's own
    prompts and column labels (a CV written on the template) are not content
    stage 2 should keep, so they are neither covered nor lost (#815: 976WPY's
    64 "lost" lines were its labels)."""
    return [l for l in source_lines if len(norm(l)) >= SUBSTANTIVE_LINE_CHARS
            and not _is_template_scaffolding(l)]


def _scrubbed_as_stage2_read_it(line: str) -> str:
    """`line` with the pre-LLM scrub applied to the form the pipeline scrubbed.

    The doctor reads source lines with real tabs (`iter_source_lines`); the
    pipeline's reader turns each tab into a space and the scrub ran on that.
    A tab is a fragment boundary to the scrub, so a label followed by two or
    more tabs is left untouched as read here (#1232, a date-of-birth label
    with four tabs before its value). Scrub the space form instead. A line
    the scrub does not change comes back as given, tabs and all, so the
    evidence for an ordinary lost line is unchanged.

    Mirrors only the per-line scrub. The pipeline also scrubs across table
    cells and across lines (a bare label with its value in the next cell or
    paragraph); none of the 163 corpus CVs differs on that, and a line that
    scrub withholds but this one does not still reads as lost, never as
    covered."""
    flat = line.replace("\t", " ")
    scrubbed = redact_pre_llm_values(flat)
    return scrubbed if scrubbed != flat else line


def _lost_lines(substantive: list[str], entries: list[Entry]) -> list[str]:
    """The substantive lines no entry covers, stripped.

    Whitespace-free comparison (see squash). Checked PER ENTRY, so a line can
    never be called covered by unrelated content scattered across the document
    (what the old \\x00-sentinel join enforced).

    Verbatim containment alone is too strict: stage 2 legitimately MERGES
    adjacent source content into one entry, inserting text mid-line --
      source: '\\t\\t1984-1989\\t\\t\\t\\tB.S.\\t (Biology)'
      entry : '1984-1989    B.S. University of Utah (Biology)'
    Nothing is lost (the entry is a superset), but the source line is no longer
    a contiguous substring. That alone scored web053 96.0% and web057 67.5%.
    So a line also counts as covered when one entry holds ALL of its tokens
    within one COMPACT stretch, at most COVERAGE_WINDOW_SLACK tokens longer
    than the line (#610: holding the same words scattered through an
    unrelated entry is not coverage). Both checks are kept: squash catches glued text with no token boundaries,
    tokens catch mid-line merges. A line is lost only if neither holds.

    A line is also covered when its pre-LLM scrub is (#1232). Stage 2 reads the
    post-scrub stream (#847), so a date-of-birth line sits in its entries as
    'Birth Date: [withheld]' and the raw source line can never match it. The
    raw form is tried first, so an artifact from before the scrub, which holds
    the real value, still counts. Each lost line is returned in its scrubbed
    form, so the evidence a lint quotes never carries the value the scrub
    withheld. The scrub is applied to the form the pipeline scrubbed (tabs as
    spaces, see `_scrubbed_as_stage2_read_it`)."""
    entry_squash = [squash(e.get("text", "")) for e in entries]
    entry_tokens = [_tokens(e.get("text", "")) for e in entries]
    entry_seqs = [_token_list(e.get("text", "")) for e in entries]

    def _covered(line: str) -> bool:
        squashed = squash(line)
        if squashed and any(squashed in es for es in entry_squash):
            return True
        line_tokens = _tokens(line)
        if not line_tokens:
            return False
        max_span = sum(line_tokens.values()) + COVERAGE_WINDOW_SLACK
        return any(line_tokens <= et and _has_compact_window(line_tokens, seq, max_span)
                   for et, seq in zip(entry_tokens, entry_seqs))

    lost: list[str] = []
    for line in substantive:
        if _covered(line):
            continue
        scrubbed = _scrubbed_as_stage2_read_it(line)
        if scrubbed != line and _covered(scrubbed):
            continue
        lost.append(scrubbed.strip())
    return lost


def count_mega_entries(entries: list[Entry]) -> int:
    """Pure: how many content entries pack MEGA_ENTRY_MIN_RECORDS or more
    record-like lines, i.e. several records fused into one entry. Shared by
    ``compute_metrics`` and the quality score's fused-entries gate, so the
    doctor's ``mega_entries`` flag and the score cannot count differently."""
    return sum(
        1 for e in entries
        if e.get("element_type") not in ("header", "break")
        and sum(1 for line in str(e.get("text", "")).split("\n")
                if looks_like_record(line)) >= MEGA_ENTRY_MIN_RECORDS)


def compute_metrics(source_lines: list[str], stage1a: Stage1A, stage2: Stage2) -> Metrics:
    """Pure: structural metrics for one CV from its source lines + stage
    1a/2 outputs. Everything the compare/lint verdicts read comes from here."""
    entries = stage2.get("entries", [])
    content = [e for e in entries if e.get("element_type") not in ("header", "break")]

    # --- text coverage: does each substantive source line survive anywhere?
    substantive = _substantive(source_lines)
    lost = _lost_lines(substantive, entries)
    coverage = 100.0 if not substantive else round(
        100.0 * (len(substantive) - len(lost)) / len(substantive), 1
    )

    # --- noise counts (dup key mirrors stage 2's filter_extraction_noise)
    seen: set[tuple[str | None, str, str]] = set()
    dups = 0
    empty = 0
    for e in entries:
        text = norm(e.get("text", ""))
        if e.get("element_type") not in ("header", "break") and not text:
            empty += 1
            continue
        key = (e.get("element_type"), text, str(e.get("element_idx_start")))
        if key in seen:
            dups += 1
        seen.add(key)

    # --- mega-entries: several record-like lines fused into one entry
    mega = count_mega_entries(entries)

    header_titles: list[str] = []
    _walk_headers(stage1a.get("hierarchy"), header_titles)

    # per_h1_content_counts (#617): informational only, see the Metrics
    # TypedDict's comment -- not read by compare_metrics()/lint_metrics().
    per_h1: dict[str, int] = {}
    for e in content:
        # hierarchy is typed as list[str] (Entry) but arrives untyped off
        # json.loads at runtime; a malformed entry with hierarchy as a bare
        # string (e.g. "Education" instead of ["Education"]) would otherwise
        # index hierarchy[0] and silently key on its first CHARACTER ("E")
        # instead of raising or falling back cleanly (#616 item i).
        hierarchy = e.get("hierarchy")
        if isinstance(hierarchy, list) and hierarchy:
            top = norm(hierarchy[0]) or "(none)"
        else:
            top = "(none)"
        per_h1[top] = per_h1.get(top, 0) + 1

    return {
        "source_lines": len(source_lines),
        "substantive_lines": len(substantive),
        "text_coverage_pct": coverage,
        "lost_lines": lost,
        "entries_total": len(entries),
        "entries_content": len(content),
        "empty_content": empty,
        "duplicate_entries": dups,
        "mega_entries": mega,
        "max_entry_chars": max((len(str(e.get("text", ""))) for e in entries), default=0),
        "headers_detected": len(header_titles),
        "header_titles": header_titles,
        "per_h1_content_counts": per_h1,
    }


class LostBlock(TypedDict):
    """One source table that stage 2 mostly lost (#815)."""
    block: int
    substantive_lines: int
    lost_lines: list[str]


# A table is a lost block when it holds at least this many substantive lines
# and at least this share of them is lost. Both calibrated in the PR (#815).
LOST_BLOCK_MIN_LINES = 3
LOST_BLOCK_MIN_LOST_SHARE = 0.5


def find_lost_blocks(block_lines: list[tuple[int, str]], stage2: Stage2) -> list[LostBlock]:
    """Pure: the source tables whose own coverage is low, whatever the rest
    of the document scores. web207 lost its whole personal-data table (name,
    address, phone) at 99.1% document coverage: five rows against 840
    entries never moves the percentage (#815). Body paragraphs (BODY_BLOCK)
    are left to the document-wide figure."""
    by_block: dict[int, list[str]] = {}
    for block, line in block_lines:
        if block != BODY_BLOCK:
            by_block.setdefault(block, []).append(line)
    entries = stage2.get("entries", [])
    found: list[LostBlock] = []
    for block, lines in by_block.items():
        substantive = _substantive(lines)
        if len(substantive) < LOST_BLOCK_MIN_LINES:
            continue
        lost = _lost_lines(substantive, entries)
        if len(lost) >= LOST_BLOCK_MIN_LOST_SHARE * len(substantive):
            found.append({"block": block, "substantive_lines": len(substantive),
                          "lost_lines": lost})
    return found


# ------------------------------------------------------------------- compare

def compare_metrics(baseline: Metrics, candidate: Metrics) -> tuple[Verdict, list[str]]:
    """Pure: verdict for one CV. Returns (verdict, reasons); verdict is
    REGRESSION / IMPROVED / OK.

    Deliberately does NOT compare max_entry_chars, per_h1_content_counts,
    entries_total, or entries_content -- they're informational-only (#617,
    see the Metrics TypedDict). Comparison stays on the loss/noise signals:
    coverage, lost lines, header titles, and _COUNT_KEYS.
    """
    reasons: list[str] = []
    b_counts, c_counts = _counts(baseline), _counts(candidate)

    drop = baseline["text_coverage_pct"] - candidate["text_coverage_pct"]
    if drop > COVERAGE_DROP_TOLERANCE_PTS:
        reasons.append(f"coverage {baseline['text_coverage_pct']}% -> {candidate['text_coverage_pct']}%")
    lost_before = set(map(norm, baseline["lost_lines"]))
    newly_lost = [l for l in candidate["lost_lines"] if norm(l) not in lost_before]
    if newly_lost:
        reasons.append(f"{len(newly_lost)} newly lost line(s), e.g. '{newly_lost[0][:60]}'")
    # Diffed unconditionally (not gated on a headers_detected count drop):
    # a same-count header REPLACEMENT -- one title swapped for another --
    # is invisible if this only runs when the count falls (#615 item 2).
    # Counter (multiset), not set: a title duplicated in baseline that loses
    # one copy is invisible to a set diff even though headers_detected (the
    # full list length) already reflects the loss (#615 item 3).
    gone_counter = Counter(baseline["header_titles"]) - Counter(candidate["header_titles"])
    if gone_counter:
        sample = next(iter(gone_counter), "?")
        reasons.append(
            f"headers {baseline['headers_detected']} -> {candidate['headers_detected']}"
            f" (lost e.g. '{sample[:40]}')"
        )
    for key in _COUNT_KEYS:
        if c_counts[key] > b_counts[key]:
            reasons.append(f"{key} {b_counts[key]} -> {c_counts[key]}")

    if reasons:
        return "REGRESSION", reasons

    improved = []
    if candidate["text_coverage_pct"] > baseline["text_coverage_pct"]:
        improved.append(f"coverage +{round(candidate['text_coverage_pct'] - baseline['text_coverage_pct'], 1)}pt")
    for key in _COUNT_KEYS:
        if c_counts[key] < b_counts[key]:
            improved.append(f"{key} {b_counts[key]} -> {c_counts[key]}")
    if candidate["headers_detected"] > baseline["headers_detected"]:
        improved.append(f"headers +{candidate['headers_detected'] - baseline['headers_detected']}")
    return ("IMPROVED", improved) if improved else ("OK", [])


def lint_metrics(metrics: Metrics) -> list[str]:
    """Pure: absolute red flags for one CV, no baseline needed."""
    flags = []
    if metrics["text_coverage_pct"] < 97.0:
        sample = metrics["lost_lines"][0][:60] if metrics["lost_lines"] else ""
        flags.append(
            f"coverage {metrics['text_coverage_pct']}% "
            f"({len(metrics['lost_lines'])} lost, e.g. '{sample}')"
        )
    for key, val in _counts(metrics).items():
        if val:
            flags.append(f"{key}={val}")
    return flags


# ---------------------------------------------------------------- snapshotting

class SegmentationRegressionError(Exception):
    """Raised by snapshot()/_load_metrics()/run_compare() on a fatal,
    user-facing condition. The error policy belongs to the driver, not the
    stage (CODING_STANDARDS §5.1): these functions raise, and main() is the
    sole place that translates that into sys.exit (#616 item v). Raising
    also makes each condition unit-testable without a SystemExit-catching
    test (the prior sys.exit() calls made these functions harder to test in
    isolation, per the issue)."""


class SnapshotLabelError(SegmentationRegressionError, ValueError):
    """A snapshot label that fails validation (#616 item iv). Also a ValueError
    for callers that treat it as a bad argument, while main() translates it to a
    clean exit like every other SegmentationRegressionError."""


# Snapshot labels build a directory path directly (_snapshot_dir below); a
# label containing '../' segments could otherwise walk outside gold_set/
# (#616 item iv). CLI-only tool, so the risk is low, but the check is cheap.
_SNAPSHOT_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _outputs_root() -> Path:
    return Path(__file__).resolve().parent / "outputs"


def _snapshot_dir(label: str) -> Path:
    if not _SNAPSHOT_LABEL_RE.fullmatch(label):
        raise SnapshotLabelError(
            f"invalid snapshot label {label!r}: must match {_SNAPSHOT_LABEL_RE.pattern}"
        )
    return _outputs_root() / "gold_set" / f"segsnap_{label}"


def _default_cv_dir() -> Path | None:
    gold_root = _outputs_root() / "gold_set"
    if not gold_root.exists():
        return None
    candidates = sorted(
        d / "originals" for d in gold_root.iterdir()
        if d.is_dir() and (d / "originals").is_dir()
    )
    return candidates[-1] if candidates else None


class SnapshotResult(TypedDict):
    """One CV's outcome from _snapshot_cv(): either metrics (error is None)
    or an error (metrics is None) -- never both, never neither."""
    uid: str
    metrics: Metrics | None
    error: str | None
    cost: float


def _snapshot_cv(docx_path: Path, snap_dir: Path) -> SnapshotResult:
    """Run stages 1a -> 1b -> 2 on ONE gold CV, persist its artifacts under
    snap_dir, and compute its metrics (#T2.1). Any Exception during that
    work is caught and returned as SnapshotResult['error'] rather than
    propagated, so one CV's failure doesn't take the whole snapshot's
    already-computed metrics down with it -- snapshot() is what isolates
    per-CV failures and keeps going; see its docstring. No retry/resume: a
    failed CV is simply reported, not automatically re-attempted -- out of
    scope for this fix."""
    from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
        get_cv_hierarchy_chunked,
    )
    from unified_pipeline.stage_1b_hierarchy_mapper import run_stage_1b
    from unified_pipeline.stage_2_entry_extraction import run_stage_2

    uid = docx_path.stem
    cv_snap = snap_dir / uid
    cv_snap.mkdir(exist_ok=True)
    cost = 0.0
    try:
        hierarchy, stats = get_cv_hierarchy_chunked(cv_path=str(docx_path))
        stage1a: Stage1A = {"document_uid": uid, "hierarchy": hierarchy, "meta": stats}
        stage1a_path = cv_snap / f"{uid}_segmented.json"
        stage1a_path.write_text(json.dumps(stage1a, indent=2), encoding="utf-8")
        cost += stats.get("extraction_cost", 0) or 0

        _, stage1b_path = run_stage_1b(str(docx_path), hierarchy_json_path=str(stage1a_path))
        stage2, _ = run_stage_2(str(docx_path), hierarchy_json_path=str(stage1b_path))
        (cv_snap / f"{uid}_entries.json").write_text(
            json.dumps(stage2, indent=2), encoding="utf-8"
        )
        cost += stage2.get("total_cost", 0) or 0

        metrics = compute_metrics(iter_source_lines(str(docx_path)), stage1a, stage2)
    except Exception as exc:
        logger.exception("snapshot: %s failed", uid)
        return {"uid": uid, "metrics": None, "error": str(exc), "cost": cost}
    return {"uid": uid, "metrics": metrics, "error": None, "cost": cost}


def snapshot(label: str, cv_dir: str | None, uids: list[str] | None) -> Path:
    """Run stages 1a -> 1b -> 2 on each gold CV and record outputs + metrics.
    Costs real LLM calls (~$0.25/CV).

    Fault isolation (#T2.1): each CV runs through _snapshot_cv(), which
    catches its own exceptions. metrics.json is written for every CV that
    succeeded even if others failed, and if ANY CV failed this still raises
    SegmentationRegressionError AFTER writing, naming the failed uids, so
    main() exits nonzero (fail closed, CODING_STANDARDS §5.5) without losing
    what was already computed. No retry/resume -- out of scope.
    """
    snap = _snapshot_dir(label)
    if (snap / "metrics.json").exists():
        raise SnapshotLabelError(
            f"Snapshot '{label}' already exists ({snap / 'metrics.json'}). "
            "Choose a new label or delete the directory."
        )

    source = Path(cv_dir) if cv_dir else _default_cv_dir()
    if not source or not source.is_dir():
        raise SegmentationRegressionError(
            "No gold CV directory found. Pass --cvs <dir of .docx files>."
        )

    docx_files = sorted(source.glob("*.docx"))
    if uids:
        docx_files = [f for f in docx_files if f.stem in set(uids)]
    if not docx_files:
        raise SegmentationRegressionError(f"No .docx files matched in {source}")

    snap.mkdir(parents=True, exist_ok=True)
    all_metrics: dict[str, Metrics] = {}
    total_cost = 0.0
    failed_uids: list[str] = []

    for docx in docx_files:
        logger.info("=== %s ===", docx.stem)
        result = _snapshot_cv(docx, snap)
        total_cost += result["cost"]
        if result["error"] is not None:
            failed_uids.append(result["uid"])
            logger.warning("  FAILED: %s", result['error'])
            continue
        metrics = result["metrics"]
        if metrics is None:
            raise SegmentationRegressionError(
                f"{result['uid']}: _snapshot_cv returned neither metrics nor error"
            )
        all_metrics[result["uid"]] = metrics
        print(f"  coverage={metrics['text_coverage_pct']}% "
              f"entries={metrics['entries_total']} mega={metrics['mega_entries']} "
              f"dups={metrics['duplicate_entries']} headers={metrics['headers_detected']}")

    (snap / "metrics.json").write_text(json.dumps(all_metrics, indent=2), encoding="utf-8")
    print(f"\nSnapshot '{label}': {len(all_metrics)} CVs, LLM cost ${total_cost:.2f}")
    print(f"Metrics: {snap / 'metrics.json'}")

    if failed_uids:
        raise SegmentationRegressionError(
            f"Snapshot '{label}': {len(failed_uids)} CV(s) failed: {', '.join(failed_uids)}"
        )
    return snap


# --------------------------------------------------------------------- report

def _load_metrics(label: str) -> dict[str, Metrics]:
    """Load one snapshot's metrics.json. Raises SegmentationRegressionError
    if the file is missing, isn't a JSON object, or a value inside it isn't
    shaped like a Metrics dict (#616 item ii: json.loads() is typed
    dict[str, Metrics] but returns Any at runtime, with no shape check)."""
    path = _snapshot_dir(label) / "metrics.json"
    if not path.exists():
        raise SegmentationRegressionError(
            f"No snapshot '{label}' ({path} missing). Run: snapshot {label}"
        )
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SegmentationRegressionError(
            f"{path}: expected a JSON object of {{uid: Metrics}}, got {type(raw).__name__}"
        )
    required_keys = Metrics.__required_keys__
    for uid, entry in raw.items():
        if not isinstance(entry, dict):
            raise SegmentationRegressionError(
                f"{path}: entry {uid!r} is a {type(entry).__name__}, not a Metrics object"
            )
        missing = required_keys - entry.keys()
        if missing:
            raise SegmentationRegressionError(
                f"{path}: entry {uid!r} is missing Metrics keys: {sorted(missing)}"
            )
        for field, (checker, description) in _METRICS_FIELD_CHECKERS.items():
            if not checker(entry[field]):
                raise SegmentationRegressionError(
                    f"{path}: entry {uid!r} field {field!r} must be "
                    f"{description}, got {entry[field]!r}"
                )
    return raw


def run_compare(baseline_label: str, candidate_label: str) -> int:
    """Diff two snapshots. Raises if they share no CVs at all; otherwise
    fails closed on a uid present in baseline but absent from candidate --
    reported as a MISSING row and counted toward regressions, rather than
    silently dropped from `shared` and the run passing with 0 regressions
    (#621; CODING_STANDARDS §5.5, "fail closed"). A uid present ONLY in the
    candidate is reported as an informational NEW row (mirroring MISSING's
    style) and is neither a regression nor an error -- a newly added CV
    cannot regress against nothing (#T2.5)."""
    baseline = _load_metrics(baseline_label)
    candidate = _load_metrics(candidate_label)
    shared = sorted(set(baseline) & set(candidate))
    missing = sorted(set(baseline) - set(candidate))
    new = sorted(set(candidate) - set(baseline))
    if not shared and not missing and not new:
        raise SegmentationRegressionError("Snapshots share no CVs — nothing to compare.")

    rows: list[tuple[str, str, str]] = []
    regressions = 0
    for uid in missing:
        regressions += 1
        rows.append((uid, "MISSING", "present in baseline, absent from candidate snapshot"))
    for uid in new:
        rows.append((uid, "NEW", "present in candidate, absent from baseline snapshot"))
    for uid in shared:
        verdict, reasons = compare_metrics(baseline[uid], candidate[uid])
        if verdict == "REGRESSION":
            regressions += 1
        rows.append((uid, verdict, "; ".join(reasons)))
    rows.sort(key=lambda row: row[0])

    width = max(len(row[0]) for row in rows)
    lines = [f"Segmentation regression: {baseline_label} -> {candidate_label}", ""]
    for uid, label, detail in rows:
        lines.append(f"{uid:<{width}}  {label:<10}  {detail}")
    lines.append("")
    total = len(shared) + len(missing) + len(new)
    missing_note = f" ({len(missing)} missing from candidate)" if missing else ""
    new_note = f" ({len(new)} new in candidate)" if new else ""
    lines.append(f"{regressions} regression(s) across {total} CVs{missing_note}{new_note}")
    report = "\n".join(lines)
    print(report)
    (_snapshot_dir(candidate_label) / "REPORT.md").write_text(report + "\n", encoding="utf-8")
    return 1 if regressions else 0


def run_lint(label: str) -> int:
    metrics = _load_metrics(label)
    flagged = 0
    for uid in sorted(metrics):
        flags = lint_metrics(metrics[uid])
        if flags:
            flagged += 1
            print(f"{uid}: " + "; ".join(flags))
        else:
            print(f"{uid}: clean")
    print(f"\n{flagged} of {len(metrics)} CVs flagged")
    return 1 if flagged else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="run stages 1a->2 on the gold CVs and record metrics")
    p_snap.add_argument("label")
    p_snap.add_argument("--cvs", help="directory of .docx files (default: latest gold_set originals)")
    p_snap.add_argument("--uids", nargs="*", help="subset of CV stems to run")

    p_cmp = sub.add_parser("compare", help="diff two snapshots; exit 1 on regression")
    p_cmp.add_argument("baseline")
    p_cmp.add_argument("candidate")

    p_lint = sub.add_parser("lint", help="absolute red flags in one snapshot")
    p_lint.add_argument("label")

    args = parser.parse_args()
    # snapshot()/run_compare()/run_lint() (via _load_metrics()) raise
    # SegmentationRegressionError on a fatal, user-facing condition; main()
    # is the one place that translates that into a process exit
    # (CODING_STANDARDS §5.1; #616 item v).
    try:
        if args.command == "snapshot":
            snapshot(args.label, args.cvs, args.uids)
            sys.exit(0)
        if args.command == "compare":
            sys.exit(run_compare(args.baseline, args.candidate))
        if args.command == "lint":
            sys.exit(run_lint(args.label))
    except SegmentationRegressionError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
