"""Lints for how the source document was cut into a hierarchy (#493).

One responsibility: whether stage 1a/2 preserved the SHAPE of the source --
every line accounted for, entries the size of a record rather than a chapter,
and every header-looking line actually promoted to a hierarchy node.

Both lints read only the source lines and the stage 1a/2 artifacts. Nothing
here looks at the rendered document: a header demoted to content misroutes
everything filed under it long before stage 6 runs, and the finding is about
the cut, not the page.

`MISSED_HEADERS_WARN_COUNT`, `_hierarchy_titles` and `_header_key` are used by
nothing else in `run_doctor.py`, so they move here and stop being
module-global. `_magnitude_severity` is shared with the extraction domain and
stays in `doctor.shared`. Bodies are unmodified; `run_doctor` re-exports every
name it exported before.
"""
from typing import Dict, List

from unified_pipeline.segmentation_regression import (
    _norm,
    compute_metrics,
    lint_metrics,
)

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


# --------------------------------------------------------------------------
# Header-looking source lines that never became a hierarchy node.
#
# The threshold is the measured p75 of headers-missed-per-run over the
# 73 scored runs of the 2026-07-25 batch, the same measurement as the
# other magnitudes. WARN means this run sits in the corpus's worst
# quartile; the corpus median is 2, and a flat WARN on any single demoted
# header is what made the verdict carry no information (#438).
MISSED_HEADERS_WARN_COUNT = 6


def _hierarchy_titles(stage1a: dict) -> list[str]:
    titles: list[str] = []

    def walk(nodes):
        for node in nodes or []:
            title = _norm(node.get("text", ""))
            if title:
                titles.append(title)
            walk(node.get("children"))

    walk(stage1a.get("hierarchy"))
    return titles


def _header_key(text: str) -> str:
    """Comparison key for header matching: normalized, trailing ':' dropped.

    Stage 1a promotes 'PROFESSIONAL SOCIETIES:' to the hierarchy node
    'PROFESSIONAL SOCIETIES' -- the colon is source formatting, not part of the
    header name. Comparing raw normalized forms reports a header that WAS
    detected as missing: on the 2026-07-15 corpus (25 CVs) that was 36 of 61
    findings (59%), including 22 of web061's 23.
    """
    return _norm(text).rstrip(":").strip()


def lint_missed_headers(candidates: list[str], stage1a: dict,
                        stage2: dict) -> list[dict]:
    """Header-looking source lines absent from the 1a hierarchy AND from
    every entry hierarchy path: a header demoted to content misroutes
    everything filed under it."""
    known = {_header_key(t) for t in _hierarchy_titles(stage1a)}
    paths = {_header_key(h) for e in stage2.get("entries", [])
             for h in (e.get("hierarchy") or [])}
    findings, seen = [], set()
    for cand in candidates:
        normed = _header_key(cand)
        if not normed or normed in seen:
            continue
        seen.add(normed)
        if normed in known or normed in paths:
            continue
        findings.append(_finding(
            "missed_headers", "WARN",
            f"header-like source line missing from segmentation: '{cand}'",
            [cand]))
    # Severity is a property of the RUN, not of each header: one stray
    # header-like line is normal, a dozen means segmentation lost the document's
    # shape. This lint emits one finding per header, so without this the finding
    # count doubled as the severity and a long CV always looked worse (#438).
    severity = _magnitude_severity(len(findings), MISSED_HEADERS_WARN_COUNT)
    for f in findings:
        f["severity"] = severity
    return findings
