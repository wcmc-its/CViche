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
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal, TypedDict

# Substantive-line threshold: shorter lines ("2016", "PhD", bare bullets)
# match by accident and only add noise to the coverage metric.
SUBSTANTIVE_LINE_CHARS = 15

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
    children: list["HierarchyNode"]


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
    max_entry_chars: int
    headers_detected: int
    header_titles: list[str]
    per_h1_content_counts: dict[str, int]


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


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Word/number token set for the coverage check (see compute_metrics)."""
    return set(_TOKEN_RE.findall(_norm(text)))


def _squash(text: str) -> str:
    """Whitespace-FREE normalization for the coverage check: stage 2 joins
    text across in-paragraph line breaks with no whitespace at all
    ('Present position:' + break + 'Attending' -> 'position:Attending'),
    and tab-joined label/value lines re-emerge with tabs dropped. Comparing
    with whitespace removed on both sides is immune to all of that."""
    return re.sub(r"\s+", "", str(text or "")).lower()


# --------------------------------------------------------------- source lines

def iter_source_lines(docx_path: str) -> list[str]:
    """Every text line of the source document, INCLUDING paragraphs inside
    table cells (recursively): CVs routinely use 1×1 layout tables as section
    containers, and a coverage metric that can't see into cells would have
    missed the 89HQVQ grant loss entirely."""
    from docx import Document  # local import: harness is optional tooling
    from unified_pipeline.core.docx_structure_extractor import get_paragraph_text

    lines: list[str] = []
    # python-docx returns the same underlying cell (_tc element) for every grid
    # position a vMerge/hMerge spans, so an unguarded walk counts a merged
    # cell's paragraphs once per spanned row/column. Mirror the identity guard
    # at core/docx_structure_extractor.py's seen_cells handling (~line 642):
    # identity is the cell's _tc element, not the _Cell wrapper (a fresh
    # wrapper is constructed on every access, so wrapper identity never
    # matches). One set shared by the top-level loop and the recursive calls
    # below covers both entry points into walk_cell (#615 item 1).
    seen_cells: set = set()

    def walk_cell(cell):
        if cell._tc in seen_cells:
            return
        seen_cells.add(cell._tc)
        for para in cell.paragraphs:
            text = get_paragraph_text(para, tab_char='\t')
            if text.strip():
                lines.append(text)
        for tbl in cell.tables:
            for row in tbl.rows:
                for c in row.cells:
                    walk_cell(c)

    doc = Document(docx_path)
    for para in doc.paragraphs:
        text = get_paragraph_text(para, tab_char='\t')
        if text.strip():
            lines.append(text)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                walk_cell(cell)
    return lines


# ------------------------------------------------------------------- metrics

def _looks_like_record(line: str) -> bool:
    line = line.strip()
    return len(line) > 60 and (" | " in line or "\t" in line)


def _walk_headers(nodes: list[HierarchyNode] | None, titles: list[str]) -> None:
    for node in nodes or []:
        title = _norm(node.get("text", ""))
        if title:
            titles.append(title)
        _walk_headers(node.get("children"), titles)


def compute_metrics(source_lines: list[str], stage1a: Stage1A, stage2: Stage2) -> Metrics:
    """Pure: structural metrics for one CV from its source lines + stage
    1a/2 outputs. Everything the compare/lint verdicts read comes from here."""
    entries = stage2.get("entries", [])
    content = [e for e in entries if e.get("element_type") not in ("header", "break")]

    # --- text coverage: does each substantive source line survive anywhere?
    # Whitespace-free comparison (see _squash). Checked PER ENTRY, so a line can
    # never be called covered by unrelated content scattered across the document
    # (what the old \x00-sentinel join enforced).
    #
    # Verbatim containment alone is too strict: stage 2 legitimately MERGES
    # adjacent source content into one entry, inserting text mid-line --
    #   source: '\t\t1984-1989\t\t\t\tB.S.\t (Biology)'
    #   entry : '1984-1989    B.S. University of Utah (Biology)'
    # Nothing is lost (the entry is a superset), but the source line is no longer
    # a contiguous substring. That alone scored web053 96.0% and web057 67.5%.
    # So a line also counts as covered when one entry holds ALL of its tokens.
    # Both checks are kept: squash catches glued text with no token boundaries,
    # tokens catch mid-line merges. A line is lost only if neither holds.
    entry_squash = [_squash(e.get("text", "")) for e in entries]
    entry_tokens = [_tokens(e.get("text", "")) for e in entries]
    substantive = [l for l in source_lines if len(_norm(l)) >= SUBSTANTIVE_LINE_CHARS]

    def _covered(line: str) -> bool:
        squashed = _squash(line)
        if squashed and any(squashed in es for es in entry_squash):
            return True
        line_tokens = _tokens(line)
        return bool(line_tokens) and any(line_tokens <= et for et in entry_tokens)

    lost = [l.strip() for l in substantive if not _covered(l)]
    coverage = 100.0 if not substantive else round(
        100.0 * (len(substantive) - len(lost)) / len(substantive), 1
    )

    # --- noise counts (dup key mirrors stage 2's filter_extraction_noise)
    seen: set[tuple[str | None, str, str]] = set()
    dups = 0
    empty = 0
    for e in entries:
        text = _norm(e.get("text", ""))
        if e.get("element_type") not in ("header", "break") and not text:
            empty += 1
            continue
        key = (e.get("element_type"), text, str(e.get("element_idx_start")))
        if key in seen:
            dups += 1
        seen.add(key)

    # --- mega-entries: several record-like lines fused into one entry
    mega = 0
    for e in content:
        records = sum(1 for line in str(e.get("text", "")).split("\n")
                      if _looks_like_record(line))
        if records >= MEGA_ENTRY_MIN_RECORDS:
            mega += 1

    header_titles: list[str] = []
    _walk_headers(stage1a.get("hierarchy"), header_titles)

    per_h1: dict[str, int] = {}
    for e in content:
        # hierarchy is typed as list[str] (Entry) but arrives untyped off
        # json.loads at runtime; a malformed entry with hierarchy as a bare
        # string (e.g. "Education" instead of ["Education"]) would otherwise
        # index hierarchy[0] and silently key on its first CHARACTER ("E")
        # instead of raising or falling back cleanly (#616 item i).
        hierarchy = e.get("hierarchy")
        if isinstance(hierarchy, list) and hierarchy:
            top = _norm(hierarchy[0]) or "(none)"
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


# ------------------------------------------------------------------- compare

def compare_metrics(baseline: Metrics, candidate: Metrics) -> tuple[Verdict, list[str]]:
    """Pure: verdict for one CV. Returns (verdict, reasons); verdict is
    REGRESSION / IMPROVED / OK."""
    reasons: list[str] = []
    b_counts, c_counts = _counts(baseline), _counts(candidate)

    drop = baseline["text_coverage_pct"] - candidate["text_coverage_pct"]
    if drop > COVERAGE_DROP_TOLERANCE_PTS:
        reasons.append(f"coverage {baseline['text_coverage_pct']}% -> {candidate['text_coverage_pct']}%")
    lost_before = set(map(_norm, baseline["lost_lines"]))
    newly_lost = [l for l in candidate["lost_lines"] if _norm(l) not in lost_before]
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

def _outputs_root() -> Path:
    return Path(__file__).resolve().parent / "outputs"


def _snapshot_dir(label: str) -> Path:
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


def snapshot(label: str, cv_dir: str | None, uids: list[str] | None) -> Path:
    """Run stages 1a -> 1b -> 2 on each gold CV and record outputs + metrics.
    Costs real LLM calls (~$0.25/CV)."""
    from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
        get_cv_hierarchy_chunked,
    )
    from unified_pipeline.stage_1b_hierarchy_mapper import run_stage_1b
    from unified_pipeline.stage_2_entry_extraction import run_stage_2

    source = Path(cv_dir) if cv_dir else _default_cv_dir()
    if not source or not source.is_dir():
        sys.exit("No gold CV directory found. Pass --cvs <dir of .docx files>.")

    docx_files = sorted(source.glob("*.docx"))
    if uids:
        docx_files = [f for f in docx_files if f.stem in set(uids)]
    if not docx_files:
        sys.exit(f"No .docx files matched in {source}")

    snap = _snapshot_dir(label)
    snap.mkdir(parents=True, exist_ok=True)
    all_metrics: dict[str, Metrics] = {}
    total_cost = 0.0

    for docx in docx_files:
        uid = docx.stem
        print(f"\n=== {uid} ===")
        cv_snap = snap / uid
        cv_snap.mkdir(exist_ok=True)

        hierarchy, stats = get_cv_hierarchy_chunked(cv_path=str(docx))
        stage1a: Stage1A = {"document_uid": uid, "hierarchy": hierarchy, "meta": stats}
        stage1a_path = cv_snap / f"{uid}_segmented.json"
        stage1a_path.write_text(json.dumps(stage1a, indent=2))
        total_cost += stats.get("extraction_cost", 0) or 0

        _, stage1b_path = run_stage_1b(str(docx), hierarchy_json_path=str(stage1a_path))
        stage2, _ = run_stage_2(str(docx), hierarchy_json_path=str(stage1b_path))
        (cv_snap / f"{uid}_entries.json").write_text(json.dumps(stage2, indent=2))
        total_cost += stage2.get("total_cost", 0) or 0

        metrics = compute_metrics(iter_source_lines(str(docx)), stage1a, stage2)
        all_metrics[uid] = metrics
        print(f"  coverage={metrics['text_coverage_pct']}% "
              f"entries={metrics['entries_total']} mega={metrics['mega_entries']} "
              f"dups={metrics['duplicate_entries']} headers={metrics['headers_detected']}")

    (snap / "metrics.json").write_text(json.dumps(all_metrics, indent=2))
    print(f"\nSnapshot '{label}': {len(all_metrics)} CVs, LLM cost ${total_cost:.2f}")
    print(f"Metrics: {snap / 'metrics.json'}")
    return snap


# --------------------------------------------------------------------- report

def _load_metrics(label: str) -> dict[str, Metrics]:
    path = _snapshot_dir(label) / "metrics.json"
    if not path.exists():
        sys.exit(f"No snapshot '{label}' ({path} missing). Run: snapshot {label}")
    return json.loads(path.read_text())


def run_compare(baseline_label: str, candidate_label: str) -> int:
    baseline = _load_metrics(baseline_label)
    candidate = _load_metrics(candidate_label)
    shared = sorted(set(baseline) & set(candidate))
    if not shared:
        sys.exit("Snapshots share no CVs — nothing to compare.")

    rows: list[tuple[str, Verdict, str]] = []
    regressions = 0
    for uid in shared:
        verdict, reasons = compare_metrics(baseline[uid], candidate[uid])
        if verdict == "REGRESSION":
            regressions += 1
        rows.append((uid, verdict, "; ".join(reasons)))

    width = max(len(u) for u in shared)
    lines = [f"Segmentation regression: {baseline_label} -> {candidate_label}", ""]
    for uid, verdict, detail in rows:
        lines.append(f"{uid:<{width}}  {verdict:<10}  {detail}")
    lines.append("")
    lines.append(f"{regressions} regression(s) across {len(shared)} CVs")
    report = "\n".join(lines)
    print(report)
    (_snapshot_dir(candidate_label) / "REPORT.md").write_text(report + "\n")
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
    if args.command == "snapshot":
        snapshot(args.label, args.cvs, args.uids)
        sys.exit(0)
    if args.command == "compare":
        sys.exit(run_compare(args.baseline, args.candidate))
    if args.command == "lint":
        sys.exit(run_lint(args.label))


if __name__ == "__main__":
    main()
