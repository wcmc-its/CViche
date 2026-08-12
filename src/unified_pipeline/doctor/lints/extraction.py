"""Lints for what the extraction stages pulled out, and what became of it (#493).

One responsibility: compare the entries and fields stages 3b/4 produced against
the source they came from and the document they landed in -- grants filed under
the wrong funding heading, entries whose field extraction covered almost none of
their text, taxonomy codes that vanished between classification and render,
dedup drops that were not duplicates.

The line against `render.py` is which side of the comparison is the subject.
These four are about the extracted record; the render lints are about the page.
`lint_classified_unrendered` reads the output blocks, but only to decide whether
a 3b classification survived -- the finding is about the classification.

The thirteen constants, regexes and helpers below them are used by nothing else
in `run_doctor.py`, so they move together and stop being module-global. Bodies
are unmodified; `run_doctor` re-exports every name it exported before.
"""
import re
from typing import Dict, List, Optional, Tuple

from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.segmentation_regression import (
    SUBSTANTIVE_LINE_CHARS,
    _looks_like_record,
    _norm,
)
from unified_pipeline.stage_6_word_template import (
    RENDER_ROUTED_CODES,
    grant_status_rebucket_target,
)

from ..shared import (
    Haystack,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
)


# --------------------------------------------------------------------------
# Grant status vs the funding subsection the grant rendered under.

# Stage 4 has no 'status' field in the M2* schemas; grant statuses live in
# the raw entry text as a labelled fragment ("Status: Not funded").
_STATUS_LABEL_RE = re.compile(r"status\s*[:\-]\s*([^|\n]+)", re.IGNORECASE)


# The funding subsection headers stage 6 renders grant tables beneath.
_FUNDING_SECTIONS = (
    ("M2A", "current research funding"),
    ("M2B", "past (completed) funding"),
    ("M2C", "pending funding"),
)


def _entry_status(entry: Dict) -> Optional[str]:
    status = (entry.get("extracted_fields") or {}).get("status")
    if status:
        return str(status)
    match = _STATUS_LABEL_RE.search(str(entry.get("text", "")))
    return match.group(1).strip() if match else None


def _funding_haystacks(blocks: List[Tuple[str, str]]) -> Dict[str, Haystack]:
    """Per-bucket Haystack of everything rendered under each of stage 6's
    funding subsection headers."""
    segments: Dict[str, List[Tuple[str, str]]] = {c: [] for c, _ in _FUNDING_SECTIONS}
    current = None
    for kind, text in blocks:
        stripped = str(text).strip()
        if kind == "p":
            normed = _norm(stripped).rstrip(":")
            code = next((c for c, title in _FUNDING_SECTIONS if normed == title), None)
            if code:
                current = code
                continue
            if _output_section_header(stripped):
                current = None
                continue
        if current:
            segments[current].append((kind, text))
    return {code: _haystacks(seg) for code, seg in segments.items()}


def lint_bucket_status(stage4: Dict, blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Grant status (extracted field, else the 'Status:' label in the raw
    entry text) vs the funding subsection the grant actually rendered under.
    Stage 6 rebuckets mis-bucketed grants at render time (#214), so the
    stage-4 code alone proves nothing; a WARN here means the rendered
    document files the grant under the wrong funding heading, or lost it."""
    titles = dict(_FUNDING_SECTIONS)
    rendered = _funding_haystacks(blocks)
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code not in ("M2A", "M2B", "M2C"):
            continue
        status = _entry_status(e)
        target, _note = grant_status_rebucket_target(status or "")
        if not target or target == code:
            continue
        verdicts = {bucket: _entry_rendered(e.get("text"), h.text, h.tokens)
                    for bucket, h in rendered.items()}
        if verdicts[target]:
            continue  # stage 6 rebucketed it correctly
        if all(v is None for v in verdicts.values()):
            continue  # too short to locate in the output either way
        hits = [b for b, v in verdicts.items() if v]
        where = (f"it rendered under {hits[0]} ('{titles[hits[0]]}')" if hits
                 else "the entry is under no funding heading at all")
        findings.append(_finding(
            "bucket_status", "WARN",
            f"entry {e.get('element_idx_start')}: status '{status}' implies "
            f"{target} ('{titles[target]}') but {where}",
            [str(e.get("text", ""))[:120]]))
    return findings


# --------------------------------------------------------------------------
# Field extraction that covered almost none of a big entry.

# Lint 4: an entry this big, with this many record-like lines, extracting
# under this coverage is a mass-loss smell, not LLM wobble.
UNDER_EXTRACTION_MAX_PCT = 40.0
UNDER_EXTRACTION_MIN_CHARS = 800
UNDER_EXTRACTION_MIN_RECORDS = 2


# _looks_like_record only sees pipe/tab rows; fused award/honor lines are
# plain newline lines carrying a leading or trailing year ("2020 AECT ...",
# "... August 2025.") — the 2Q1_ZQ honors mega-entry (19% coverage) was
# invisible without counting them (#229).
_YEAR_EDGE_LINE_RE = re.compile(
    r"^\s*(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\s*[.)]?\s*$")


def lint_under_extraction(stage4: Dict) -> List[Dict]:
    """Large multi-record entries whose stage-4 field extraction covered
    almost none of the text: the rest of the records silently vanish."""
    findings = []
    for e in stage4.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        pct = (e.get("extraction_coverage") or {}).get("extraction_coverage_percent")
        if pct is None or pct >= UNDER_EXTRACTION_MAX_PCT:
            continue
        text = str(e.get("text", ""))
        if len(text) <= UNDER_EXTRACTION_MIN_CHARS:
            continue
        records = sum(
            1 for line in text.split("\n")
            if _looks_like_record(line)
            or (len(line.strip()) >= SUBSTANTIVE_LINE_CHARS
                and _YEAR_EDGE_LINE_RE.search(line)))
        if records < UNDER_EXTRACTION_MIN_RECORDS:
            continue
        findings.append(_finding(
            "under_extraction", "WARN",
            f"entry {e.get('element_idx_start')}: extraction coverage {pct}% "
            f"on a {len(text)}-char entry with {records} record-like lines",
            [text[:120]]))
    return findings


# --------------------------------------------------------------------------
# 3b classifications that reached no part of the output.
#
# The threshold is the measured p75 of entries-lost-per-run over the
# 73 scored runs of the 2026-07-25 batch -- the same measurement as
# the render magnitudes. WARN means this run sits in the corpus's
# worst quartile, not that the lint fired at all (#438).
CLASSIFIED_UNRENDERED_WARN_ENTRIES = 2


def _entry_rendered(text, haystack: str, haystack_tokens: set) -> Optional[bool]:
    """Whether an entry's text surfaces in the output: verbatim piece
    containment first, then distinctive-token overlap over the whole text and
    each fragment (stages 4-6 re-render entries from extracted fields, so no
    verbatim piece survives the 5c/5d formatters, and stage 6 keeps the
    title/institution fields while dropping long narratives). None = too
    short to verify either way."""
    pieces = _entry_pieces(text)
    if any(piece in haystack for piece in pieces):
        return True
    verifiable = bool(pieces)
    for chunk in [str(text or "")] + entry_fragments(text):
        tokens = _long_word_tokens(chunk)
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        verifiable = True
        if len(tokens & haystack_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP:
            return True
    return False if verifiable else None


def lint_classified_unrendered(stage3b: Dict,
                               blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Taxonomy codes classified at 3b none of whose entries appear anywhere
    in the stage-6 output (paragraphs or tables); 'T' is skipped (appendix
    catch-all)."""
    h = _haystacks(blocks)
    by_code: Dict[str, List[Dict]] = {}
    for e in stage3b.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        code = e.get("taxonomy_code")
        if not code or code == "T":
            continue
        by_code.setdefault(code, []).append(e)

    findings = []
    lost = 0
    for code in sorted(by_code):
        entries = by_code[code]
        verdicts = [(_entry_rendered(e.get("text"), h.text, h.tokens), e)
                    for e in entries]
        verifiable = [(v, e) for v, e in verdicts if v is not None]
        if not verifiable or any(v for v, _ in verifiable):
            continue
        findings.append(_finding(
            "classified_unrendered", "WARN",
            f"taxonomy code {code}: none of its {len(entries)} classified "
            f"entries appear in the output document",
            [str(e.get("text", ""))[:80] for _, e in verifiable[:3]]))
        lost += len(entries)
    # As with missed_headers, the magnitude that matters is how much of the
    # document went missing across all codes, not that one code did (#438).
    severity = _magnitude_severity(lost, CLASSIFIED_UNRENDERED_WARN_ENTRIES)
    for f in findings:
        f["severity"] = severity
    return findings


# --------------------------------------------------------------------------
# Taxonomy codes stage 3b can assign that stage 6 has no render route for.

# Codes a `_fill_*` method reads and renders directly, but that were never
# added to RENDER_ROUTED_CODES: E/G match on the source heading rather than
# a taxonomy code (stage6/sections/passthrough.py), and N4 is pulled via
# entries_by_code.get('N4', ...) in stage6/sections/mentoring.py (added by
# #261's fix, which gave N4 a render path without also adding it here). All
# three DO render -- and BECAUSE they're absent from RENDER_ROUTED_CODES,
# their entries also fall through to the appendix a second time. That's a
# real defect (duplicate content, tracked for G at #294 and for N4 at #587),
# but it is a different defect from "no render route at all", which is what
# this lint exists to catch -- flagging these here would conflate the two.
#
# This is itself a second, hand-maintained source of truth for stage-6
# routing (review on #588) -- a code silently added here without a real
# passthrough route would make this lint wrongly stay quiet about it.
# test_taxonomy_code_render_coverage.py's
# test_render_exceptions_still_wired_into_generate() is a cheap guard
# against the two hooks these three codes depend on being removed without
# updating this set; it can't prove a *new* addition is correct, only that
# the existing ones haven't silently gone stale.
_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES = frozenset({'E', 'G', 'N4'})


def lint_taxonomy_code_coverage(stage3b: Dict) -> List[Dict]:
    """Entries classified into a taxonomy code stage 6 has no render route
    for at all -- they land in the Appendix by construction, regardless of
    confidence or content (#529, e.g. N2 "Institutional Training Grants and
    Mentored Trainee Grants").

    Distinct from lint_classified_unrendered just above: that lint asks
    whether an entry's text appears ANYWHERE in the rendered output, and
    appendix content passes that check (the text is there, just in the
    Appendix), so it cannot see this class -- #529's own investigation hit
    exactly that blind spot. This lint instead asks a structural question
    that needs no rendered document at all: does this code have a dispatch
    path in generate()."""
    by_code: Dict[str, int] = {}
    for e in stage3b.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        code = e.get("taxonomy_code")
        if not code or code == "T" or code == "M1":
            # M1 is normally routed; it only falls through when the Stage
            # 4.5 summary itself didn't render, a distinct, already-covered
            # case (#317). Flagging it here would false-positive the common
            # path.
            continue
        if code in RENDER_ROUTED_CODES or code in _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES:
            continue
        by_code[code] = by_code.get(code, 0) + 1

    return [
        _finding(
            "taxonomy_code_coverage", "WARN",
            f"taxonomy code {code}: {count} entries classified but stage 6 "
            f"has no render route for this code -- routed to the Appendix "
            f"by construction, not by content or confidence",
            [code],
        )
        for code, count in sorted(by_code.items())
    ]


# --------------------------------------------------------------------------
# Stage 6 dedup drops that were not duplicates.

# A dropped entry this well contained (token-wise) in the kept entry is a
# true duplicate; anything below carries content the kept entry lacks. On
# 2Q1_ZQ the one true duplicate scored 1.00 and the seven real losses
# 0.60-0.89 (#227).
DEDUP_SAFE_CONTAINMENT = 0.9
_DEDUP_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _alphanumeric_tokens(text) -> set:
    """a-z0-9 token set for one string (lint 11 dedup-containment coverage)."""
    return set(_DEDUP_TOKEN_RE.findall(_norm(text)))


def lint_dedup_drops(report: Dict) -> List[Dict]:
    """Stage-6 dedup decisions whose dropped text is NOT near-fully contained
    in the kept entry: at loose similarity thresholds these are distinct
    records lost, not duplicates (#227)."""
    suspect = []
    for d in report.get("dedup_decisions", []):
        dropped = _alphanumeric_tokens(d.get("dropped_text", ""))
        kept = _alphanumeric_tokens(d.get("kept_text", ""))
        if not dropped:
            continue
        coverage = len(dropped & kept) / len(dropped)
        if coverage >= DEDUP_SAFE_CONTAINMENT:
            continue
        suspect.append(
            f"{d.get('code', '?')} ({d.get('metric', '?')}, {coverage:.0%} "
            f"covered by kept): dropped '{d.get('dropped_text', '')[:80]}' "
            f"vs kept '{d.get('kept_text', '')[:80]}'")
    if not suspect:
        return []
    return [_finding(
        "dedup_drops", "WARN",
        f"{len(suspect)} dedup drop(s) poorly covered by the kept entry — "
        f"possible distinct records lost (#227)",
        suspect[:6])]
