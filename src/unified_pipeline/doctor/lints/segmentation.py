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

import re

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


#: A leading enumeration token stripped before comparing header keys (#814):
#: roman ('I.', 'XI.'), arabic ('1.', '1.1.'), or a single letter ('A.').
#: Applied to lowercase, already-`_norm`ed text -- 'I.' below is 'i.'.
#: Stage 1a promotes 'I.  CURRENT POSITION' to the hierarchy node
#: 'CURRENT POSITION', without the numeral; comparing raw normalized forms
#: reports every one of those headers as missing (web199: 11 of 11).
_ENUM_PREFIX_RE = re.compile(r"^(?:[ivxlc]+\.\s*|\d+(?:\.\d+)*\.?\s*|[a-z]\.\s*)")

#: Guard for the prefix/suffix match below: a short key ('and') is a prefix
#: or suffix of countless unrelated titles and must not match that way (#814)
#: -- only an exact `_header_key` hit counts for a candidate this short.
MIN_HEADER_KEY_CHARS = 12
MIN_HEADER_KEY_WORDS = 2


def _header_key(text: str) -> str:
    """Comparison key for header matching: normalized, trailing ':' dropped,
    then a leading enumeration token stripped (#814).

    Stage 1a promotes 'PROFESSIONAL SOCIETIES:' to the hierarchy node
    'PROFESSIONAL SOCIETIES' -- the colon is source formatting, not part of the
    header name. Comparing raw normalized forms reports a header that WAS
    detected as missing: on the 2026-07-15 corpus (25 CVs) that was 36 of 61
    findings (59%), including 22 of web061's 23. The same is true of a leading
    'I.'/'1.'/'A.' section numeral: stage 1a strips it, so comparing the raw
    forms reports every enumerated section as missing too.
    """
    normed = _norm(text).rstrip(":").strip()
    return _ENUM_PREFIX_RE.sub("", normed, count=1).strip()


def _is_substantial_key(key: str) -> bool:
    """Long enough to match a longer title by prefix/suffix without risking
    a short word matching everything (#814)."""
    return len(key) >= MIN_HEADER_KEY_CHARS or len(key.split()) >= MIN_HEADER_KEY_WORDS


def _key_matches_title(key: str, titles: set[str]) -> bool:
    """Is `key` (already past the exact-match check) a prefix or suffix of
    some known hierarchy title, after the short-candidate guard (#814)?

    Covers a header wrapped over two source lines: one physical line is a
    genuine prefix of the joined title on its own ('SERVICE ON NATIONAL GRANT
    REVIEW PANELS, STUDY SECTIONS,' is a prefix of '... SECTIONS, COMMITTEES')
    even before the two lines are joined below."""
    if not _is_substantial_key(key):
        return False
    return any(title.startswith(key) or title.endswith(key) for title in titles)


def lint_missed_headers(candidates: list[str], stage1a: dict,
                        stage2: dict) -> list[dict]:
    """Header-looking source lines absent from the 1a hierarchy AND from
    every entry hierarchy path: a header demoted to content misroutes
    everything filed under it."""
    known = {_header_key(t) for t in _hierarchy_titles(stage1a)}
    paths = {_header_key(h) for e in stage2.get("entries", [])
             for h in (e.get("hierarchy") or [])}
    titles = known | paths

    findings, seen = [], set()
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
        if joined and (joined in titles or _key_matches_title(joined, titles)):
            seen.add(normed)
            seen.add(_header_key(nxt))
            skip_next = True
            continue

        seen.add(normed)
        if normed in titles or _key_matches_title(normed, titles):
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
