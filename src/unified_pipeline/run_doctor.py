"""Cross-stage run doctor: offline lints over one run's stage artifacts.

Each lint encodes an observed production failure class (run 89HQVQ lost 7 of
8 grants across several of them at once). The doctor reads the artifacts a
run leaves in the standard outputs layout (stage_*/<uid>*_*.json plus the
source and stage-6 WCM docx), applies pure structural checks — no LLM calls,
no network — and reports findings ranked ERROR/WARN/INFO.

Lints, ranked by the severity of the failure class they catch:

1. segmentation           coverage / lost lines / mega-entries / dups via the
                          segmentation_regression metrics (8 grants fused
                          into one table-cell entry)
2. missed_headers         ALL-CAPS bold header-like source lines absent from
                          the 1a hierarchy AND every entry hierarchy path
                          ('PROFESSIONAL EXPERIENCE' demoted to content)
3. bucket_status          grant status vs the funding subsection the grant
                          actually rendered under in the stage-6 document
                          (the #214 rebucketing rules)
4. under_extraction       big multi-record entries with very low stage-4
                          extraction coverage (14.9%-coverage mega-entry)
5. classified_unrendered  3b taxonomy codes none of whose entries surface in
                          the stage-6 output document (text or tables)
6. output_hygiene         bracketed taxonomy-code leaks ('• [M2A]'), appendix
                          size, boilerplate rendered in the appendix
7. dead_sections          substantive source sections whose name-matched WCM
                          output section is empty
8. unrendered_records     record lines of a fused multi-record stage-4 entry
                          definitively absent from the stage-6 output — the
                          structured-fields-only render paths drop the
                          unextracted remainder with no bullet fallback (#221)
9. enrichment_failures    stage-5 PubMed enrichment lookups that failed, so
                          those citations degrade to CV-extracted fields (#222)
10. stage6_render_warnings stage 6's own post-generation self-check findings,
                          re-emitted from the render-warnings sidecar — they
                          used to die in the pod log (#228)
11. dedup_drops           stage-6 dedup decisions whose dropped text is not
                          near-fully contained in the kept entry — at loose
                          thresholds these are distinct records lost, not
                          duplicates (#227: 7 of 8 drops on 2Q1_ZQ were real)
12. pipe_leaks            raw ' | '-delimited source lines rendered as output
                          paragraphs — verbatim-fallback formatting reaching
                          the faculty-facing document (#208 costs)
13. table_shape           honors-table rows that are mis-shaped: citation
                          blobs in the name cell, empty date column with a
                          year in the name, state-abbrev organizations,
                          organization duplicated inside the name (#229)

Lints 14-15 are the quality-score HARD-FAIL gates and sit outside that
ranking: they are the only ERROR-by-construction lints, because each one on
its own caps quality_score.py's final score into the RED do-not-deliver band.
Without them an undeliverable run reported worst=WARN like every healthy one
(#437). Each calls quality_score.py's own predicate over the same artifacts
the scorer reads, so the doctor reports the gate rather than a second
definition of it -- what that buys is that the two cannot drift apart, NOT
independent confirmation that the gate itself is calibrated:

14. owner_contact_missing the stage-4 'cv_owner' block carries no usable name,
                          or a run that produced output has no stage-4
                          artifact at all — either way the score is capped at
                          25. The one lint that does not skip on a missing
                          artifact, because the gate trips on that too.
15. pipeline_errors_present a fatal error (NameError, traceback) recorded in an
                          'error' field of stage_2/stage_3b/stage_4 — the JSON
                          the deployed scorer globs — capped at 40

Usage:

    PYTHONPATH=src python -m unified_pipeline.run_doctor <root> <uid> \
        [--source cv.docx] [--out report.json]

The CLI writes <uid>_doctor.json into the root (or --out), prints a summary,
and exits 1 if any finding is WARN or worse. The library entry point
run_doctor(root, uid, source=None) -> dict never calls sys.exit; missing or
unreadable artifacts skip their lints with an INFO note instead of crashing.
"""

import argparse
import json
import logging
import os
import re
import sys
from functools import partial
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)
from unified_pipeline.quality_score import (
    FATAL_ERROR_PATTERN,
    cv_owner_name_missing,
    iter_error_fields,
)
from unified_pipeline.segmentation_regression import (
    SUBSTANTIVE_LINE_CHARS,
    _looks_like_record,
    _norm,
    _squash,
    compute_metrics,
    iter_source_lines,
    lint_metrics,
)
from unified_pipeline.stage_6_word_template import grant_status_rebucket_target

logger = logging.getLogger(__name__)

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

# Lint 5: a squashed text piece shorter than this matches by accident; a
# longer fragment is matched by its leading window, so a reformatted tail
# (5d trims trailing publisher details) doesn't hide a rendered line.
RENDER_PIECE_MIN_CHARS = 15
RENDER_PIECE_WINDOW = 40

# Lints 3/5 fallback: stages 4-6 re-render most entries from extracted fields
# (5c teaching / 5d citation formatters), so no verbatim piece survives; an
# entry counts as rendered when its whole text — or any single fragment of it
# (stage 6 renders the extracted title/institution fields and drops long
# narratives) — has at least this many distinctive tokens and this share of
# them appear in the output.
RENDER_TOKEN_MIN_COUNT = 3
RENDER_TOKEN_OVERLAP = 0.7
_RENDER_TOKEN_RE = re.compile(r"[a-z]{5,}")

# Separator joined between output lines in the containment haystack so a
# verbatim piece can't match across two unrelated lines (a control char that
# never occurs in real CV text).
_LINE_SENTINEL = "\x00"


def _long_word_tokens(text) -> set:
    """5+-letter token set for one string (lints 5/8 render-overlap checks)."""
    return set(_RENDER_TOKEN_RE.findall(_norm(text)))


def _alphanumeric_tokens(text) -> set:
    """a-z0-9 token set for one string (lint 11 dedup-containment coverage)."""
    return set(_DEDUP_TOKEN_RE.findall(_norm(text)))


# Lint 6: an appendix bigger than this means mapping failed at scale.
APPENDIX_WARN_ENTRIES = 15

# Lint 7: a source section with at least this many substantive lines whose
# output section is empty did not just "have nothing to say".
DEAD_SECTION_MIN_LINES = 3

# Lint 8: an entry is a fused multi-record candidate at this many record-like
# lines. _looks_like_record only sees pipe/tab rows; employment/appointment
# records are date-range-prefixed comma lines ("Jun 2020-Jun 2025, Assistant
# Professor"), caught by the prefix pattern when the line carries a payload
# beyond the bare date range.
UNRENDERED_MIN_RECORD_LINES = 2
RECORD_DATE_LINE_MIN_CHARS = 20
_RECORD_DATE_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]")

SEVERITY_ORDER = ("ERROR", "WARN", "INFO")  # most to least severe

# A well-formed report is a few KB. A malformed artifact with huge text fields
# could inflate the findings into a multi-MB file; cap it. Evidence strings are
# already sliced at construction, so the only way to blow the cap is a very
# large NUMBER of findings — truncating the list is the effective lever.
MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_REPORT_FINDINGS = 1000

# '• [M2A] ...' style taxonomy-code leak (the pre-#214 appendix format).
_BRACKET_CODE_RE = re.compile(r"\[[A-Z]\d?[A-Z]?\d?\]")

# WCM output section headers come letter-prefixed ("T. APPENDIX") or as plain
# uppercase paragraphs ("RESEARCH", "MENTORING") — stage 6 emits both forms.
_SECTION_HEADER_RE = re.compile(r"^[A-Z]\.\s+\S")
_APPENDIX_HEADER = "T. APPENDIX"


def _output_section_header(text: str) -> Optional[str]:
    """Normalized section name when a paragraph is a WCM output section
    header (either form above), else None."""
    stripped = str(text or "").strip()
    if _SECTION_HEADER_RE.match(stripped):
        return _norm(re.sub(r"^[A-Z]\.\s+", "", stripped))
    if (3 <= len(stripped) <= 60 and stripped[0].isalpha()
            and stripped == stripped.upper()
            and not any(ch.isdigit() for ch in stripped)):
        return _norm(stripped)
    return None

# Appendix entries render as "• text" (bullets) or "1. text" (numbered).
_APPENDIX_ENTRY_RE = re.compile(r"^(•|\d+\.)\s+")

# Stage 4 has no 'status' field in the M2* schemas; grant statuses live in
# the raw entry text as a labelled fragment ("Status: Not funded").
_STATUS_LABEL_RE = re.compile(r"status\s*[:\-]\s*([^|\n]+)", re.IGNORECASE)

# The funding subsection headers stage 6 renders grant tables beneath.
_FUNDING_SECTIONS = (
    ("M2A", "current research funding"),
    ("M2B", "past (completed) funding"),
    ("M2C", "pending funding"),
)


def _finding(lint: str, severity: str, message: str,
             evidence: Optional[List[str]] = None) -> Dict:
    return {"lint": lint, "severity": severity, "message": message,
            "evidence": evidence or []}


def _hierarchy_titles(stage1a: Dict) -> List[str]:
    titles: List[str] = []

    def walk(nodes):
        for node in nodes or []:
            title = _norm(node.get("text", ""))
            if title:
                titles.append(title)
            walk(node.get("children"))

    walk(stage1a.get("hierarchy"))
    return titles


# ----------------------------------------------------------------- docx views

def _get_docx_document():
    """Lazy python-docx import. The doctor is optional tooling and must import
    cleanly where python-docx isn't installed (JSON-only lints still run), so
    the import stays out of module scope -- but it's the SAME import in three
    docx views, so centralize it here and give a clear message when missing."""
    try:
        from docx import Document
    except ImportError as e:  # pragma: no cover - only when the extra is absent
        raise ImportError("python-docx is required for the docx lints: "
                          "pip install python-docx") from e
    return Document


#: A credential in name-suffix position (', MD' / ', Ph.D.') -- the marker of a
#: person line rather than a section header. Anchored on the comma so headers
#: that merely contain commas are not swallowed.
_NAME_CREDENTIAL_RE = re.compile(
    r",\s*(?:M\.?D\.?|D\.?O\.?|Ph\.?\s?D\.?|M\.?B\.?B\.?S\.?|MBA|MPH|MSc?|"
    r"FACEP|FAAEM|FACS|FACP|CPE|DDS|DMD|DVM|JD|RN|PA-C)\b",
    re.IGNORECASE,
)


def iter_header_candidates(docx_path: str) -> List[str]:
    """Header-looking source lines: short, letters-only, ALL-CAPS bold (or
    styled as a Heading), from top-level paragraphs and single-column table
    cells (the 1x1 layout tables CVs use as section containers). Multi-column
    tables are data tables — their bold cells are column headers — and
    document furniture ('CURRICULUM VITAE', revision stamps) is not a header
    either. These are what stage 1a should have promoted to hierarchy nodes."""
    Document = _get_docx_document()

    candidates: List[str] = []

    def consider(para):
        text = para.text.strip()
        if not 3 <= len(text) <= 60:
            return
        if not any(ch.isalpha() for ch in text) or any(ch.isdigit() for ch in text):
            return
        if is_source_boilerplate(text):
            return
        # A section header is a standalone label. A tab means 'label<TAB>value'
        # -- a data row that happens to be styled like a heading, e.g. the
        # licensure rows 'Active\t\t\tMaryland' / 'Certification:\t\t\tAmerican
        # Board of Surgery'. Segmentation is right not to promote these.
        if "\t" in text:
            return
        # The owner's name/credential line is document furniture, not a section:
        # 'STANLEY J. SZEFLER, M.D.', 'LEE W. SHOCKLEY, MD, MBA, FACEP, FAAEM,
        # CPE', 'CURRICULUM VITAE - JEFFREY R OLSEN, MD'. Anchored on the
        # comma-suffix position so real headers that merely contain a comma
        # ('ADMINISTRATIVE APPOINTMENTS, SCHOOL OF MEDICINE, CU:') survive.
        if _NAME_CREDENTIAL_RE.search(text):
            return
        style = getattr(para.style, "name", "") or ""
        if style.startswith("Heading"):
            candidates.append(text)
            return
        if text != text.upper():
            return
        runs = [r for r in para.runs if r.text.strip()]
        if runs and all(r.bold for r in runs):
            candidates.append(text)

    def walk_table(tbl):
        if len(tbl.columns) != 1:
            return
        for row in tbl.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    consider(para)
                for nested in cell.tables:
                    walk_table(nested)

    doc = Document(docx_path)
    for para in doc.paragraphs:
        consider(para)
    for tbl in doc.tables:
        walk_table(tbl)
    return candidates


def _docx_text(element) -> str:
    """All ``w:t`` text under a docx element in document order. Unlike
    python-docx's ``.text``, this INCLUDES text inside tracked-change ``<w:ins>``
    runs and EXCLUDES ``<w:delText>`` — the accepted-changes view a reader sees.
    Stage 6 inserts LLM-enriched content (research summaries, reformatted
    citations) as tracked INSERTIONS, so a reader that ignores ``<w:ins>``
    under-reports what actually rendered and false-flags content as 'unrendered'
    (issue #249: M1 summaries and reformatted citations read as dropped)."""
    from docx.oxml.ns import qn
    return "".join(node.text or "" for node in element.iter(qn("w:t")))


def _cell_text(cell) -> str:
    """Track-change-aware equivalent of ``cell.text``: the cell's own paragraphs
    (nested tables excluded, matching python-docx), including ``<w:ins>`` text."""
    return "\n".join(_docx_text(p._p) for p in cell.paragraphs)


def _table_lines(tbl) -> List[str]:
    """Text lines of one Word table: each non-empty cell, nested tables
    recursed into (cell.text never surfaces them), and every row with more
    than one non-empty cell ALSO joined as one line — a record rendered as a
    structured row (label/value cells) keeps its tokens together the way one
    source line does only in the joined view. KEEP IN SYNC with the by-name
    mirror in stage_6_word_template.py's _rendered_output_lines() (the #221
    recovery pass, PR #225): both sides must agree on what counts as
    rendered. Extra lines only ever prove presence — strictly fewer false
    'absent' verdicts, never more."""
    lines: List[str] = []
    for row in tbl.rows:
        cell_texts = []
        for cell in row.cells:
            ctext = _cell_text(cell)
            if ctext.strip():
                cell_texts.append(ctext)
                lines.append(ctext)
            for nested in cell.tables:
                lines.extend(_table_lines(nested))
        if len(cell_texts) > 1:
            lines.append(" | ".join(" ".join(t.split()) for t in cell_texts))
    return lines


def read_docx_blocks(docx_path: str) -> List[Tuple[str, str]]:
    """Body-order blocks of a docx: ("p", text) per paragraph, ("table",
    _table_lines joined by newlines) per table. Grants render as one Word
    table per grant, so any output check must read tables AND paragraphs."""
    Document = _get_docx_document()
    from docx.oxml.ns import qn
    from docx.table import Table

    doc = Document(docx_path)
    blocks: List[Tuple[str, str]] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(("p", _docx_text(child)))
        elif child.tag == qn("w:tbl"):
            blocks.append(("table", "\n".join(_table_lines(Table(child, doc)))))
    return blocks


def read_docx_table_rows(docx_path: str) -> List[List[List[str]]]:
    """Raw per-row cell texts of every top-level table, EMPTY CELLS INCLUDED
    — _table_lines drops empty cells, which hides an empty date column from
    the shape checks (lint 13)."""
    Document = _get_docx_document()

    doc = Document(docx_path)
    return [[[_cell_text(cell).strip() for cell in row.cells] for row in tbl.rows]
            for tbl in doc.tables]


class Haystack(NamedTuple):
    text: str    # squashed containment haystack, _LINE_SENTINEL-joined
    tokens: set  # distinctive long-word token set


def _haystacks(blocks: List[Tuple[str, str]]) -> Haystack:
    """Containment haystack + distinctive-token set for the output blocks.
    Read the result via its named fields (``h.text`` / ``h.tokens``), not
    positional unpacking."""
    pieces: List[str] = []
    tokens: set = set()
    for _, text in blocks:
        for line in str(text).split("\n"):
            if line.strip():
                pieces.append(_squash(line))
                tokens.update(_long_word_tokens(line))
    return Haystack(_LINE_SENTINEL.join(pieces), tokens)


# -------------------------------------------------------------------- lint 1

def lint_segmentation(source_lines: List[str], stage1a: Dict,
                      stage2: Dict) -> List[Dict]:
    """Coverage / lost lines / mega-entries / dups / empties, reusing the
    segmentation_regression metrics (source docx + stage 1a + stage 2)."""
    metrics = compute_metrics(source_lines, stage1a, stage2)
    findings = []
    for flag in lint_metrics(metrics):
        evidence = ([line[:100] for line in metrics["lost_lines"][:5]]
                    if flag.startswith("coverage") else [])
        findings.append(_finding("segmentation", "WARN", flag, evidence))
    return findings


# -------------------------------------------------------------------- lint 2

def _header_key(text: str) -> str:
    """Comparison key for header matching: normalized, trailing ':' dropped.

    Stage 1a promotes 'PROFESSIONAL SOCIETIES:' to the hierarchy node
    'PROFESSIONAL SOCIETIES' -- the colon is source formatting, not part of the
    header name. Comparing raw normalized forms reports a header that WAS
    detected as missing: on the 2026-07-15 corpus (25 CVs) that was 36 of 61
    findings (59%), including 22 of web061's 23.
    """
    return _norm(text).rstrip(":").strip()


def lint_missed_headers(candidates: List[str], stage1a: Dict,
                        stage2: Dict) -> List[Dict]:
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
    return findings


# -------------------------------------------------------------------- lint 3

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


# -------------------------------------------------------------------- lint 4

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


# -------------------------------------------------------------------- lint 5

def _entry_pieces(text) -> List[str]:
    """Squashed fragments of an entry long enough to be looked up in the
    output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces


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
    return findings


# -------------------------------------------------------------------- lint 6

def _is_appendix_noise(text: str) -> bool:
    normed = " ".join(str(text or "").split())
    if not normed:
        return True
    if is_template_instruction(normed):
        return True
    return is_source_boilerplate(normed)


def lint_output_hygiene(blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Bracketed taxonomy-code leaks anywhere in the output, plus appendix
    size and boilerplate lines rendered as appendix entries."""
    findings = []
    leaks = []
    for _, text in blocks:
        for line in str(text).split("\n"):
            if _BRACKET_CODE_RE.search(line):
                leaks.append(line.strip())
    if leaks:
        findings.append(_finding(
            "output_hygiene", "ERROR",
            f"{len(leaks)} bracketed taxonomy-code leak(s) in output text",
            [leak[:100] for leak in leaks[:5]]))

    paras = [text for kind, text in blocks if kind == "p"]
    appendix_at = next((i for i, t in enumerate(paras)
                        if t.strip() == _APPENDIX_HEADER), None)
    if appendix_at is None:
        return findings

    entries = []
    for text in paras[appendix_at + 1:]:
        stripped = text.strip()
        if _output_section_header(stripped):
            break
        if _APPENDIX_ENTRY_RE.match(stripped):
            entries.append(_APPENDIX_ENTRY_RE.sub("", stripped).strip())

    boiler = [e for e in entries if _is_appendix_noise(e)]
    if boiler:
        findings.append(_finding(
            "output_hygiene", "WARN",
            f"{len(boiler)} boilerplate line(s) rendered in the appendix",
            [b[:100] for b in boiler[:5]]))
    findings.append(_finding(
        "output_hygiene",
        "WARN" if len(entries) > APPENDIX_WARN_ENTRIES else "INFO",
        f"appendix holds {len(entries)} unmapped entr"
        + ("y" if len(entries) == 1 else "ies")))
    return findings


# -------------------------------------------------------------------- lint 7

def _names_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 6 and shorter in longer


def lint_dead_sections(stage2: Dict,
                       blocks: List[Tuple[str, str]]) -> List[Dict]:
    """A source section with several substantive lines (grouped by each
    entry's top-level hierarchy header, stage 2) whose name-matched WCM
    output section holds nothing beyond template scaffolding — neither
    paragraphs nor tables."""
    per_h1: Dict[str, int] = {}
    for e in stage2.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        top = _norm((e.get("hierarchy") or ["(none)"])[0]) or "(none)"
        lines = sum(1 for line in str(e.get("text", "")).split("\n")
                    if len(_norm(line)) >= SUBSTANTIVE_LINE_CHARS)
        per_h1[top] = per_h1.get(top, 0) + lines

    sections: List[List] = []  # [raw title, normalized name, substantive lines]
    current = None
    for kind, text in blocks:
        stripped = str(text).strip()
        name = _output_section_header(stripped) if kind == "p" else None
        if name:
            current = [stripped, name, 0]
            sections.append(current)
            continue
        if current is None:
            continue
        current[2] += sum(1 for line in str(text).split("\n")
                          if len(_norm(line)) >= SUBSTANTIVE_LINE_CHARS
                          and not is_template_instruction(line))

    findings = []
    for h1, n_lines in sorted(per_h1.items()):
        if h1 == "(none)" or n_lines < DEAD_SECTION_MIN_LINES:
            continue
        matched = [s for s in sections if _names_match(h1, s[1])]
        if matched and all(s[2] == 0 for s in matched):
            findings.append(_finding(
                "dead_sections", "WARN",
                f"source section '{h1}' has {n_lines} substantive line(s) "
                f"but matching output section '{matched[0][0]}' is empty"))
    return findings


# -------------------------------------------------------------------- lint 8

def _record_lines(text) -> List[str]:
    return [line.strip() for line in str(text or "").split("\n")
            if _looks_like_record(line)
            or (len(line.strip()) >= RECORD_DATE_LINE_MIN_CHARS
                and _RECORD_DATE_PREFIX_RE.match(line.strip()))]


def _line_token_sets(blocks: List[Tuple[str, str]]) -> List[set]:
    """Distinctive-token set per OUTPUT LINE. Lint 8 verifies each record line
    against single output lines: the pooled document tokens of _haystacks let
    common academic words scattered across unrelated sections vouch for a
    dropped record, while a 5c/5d-reformatted citation still matches here
    because its surname/title tokens stay together on one line."""
    return [_long_word_tokens(line)
            for _, text in blocks for line in str(text).split("\n")
            if line.strip()]


def _record_rendered(line: str, haystack: str,
                     line_token_sets: List[set]) -> Optional[bool]:
    """Whether one record line surfaces in the output: verbatim piece first,
    then per-output-line token overlap. Verbatim absence alone proves nothing
    (stage 6 reformats dates/fields), so False requires a token-verifiable
    miss; a line without enough distinctive tokens is None, not missing."""
    if any(piece in haystack for piece in _entry_pieces(line)):
        return True
    rendered = None
    for chunk in [line] + entry_fragments(line):
        tokens = _long_word_tokens(chunk)
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        if any(len(tokens & line_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP
               for line_tokens in line_token_sets):
            return True
        rendered = False
    return rendered


def lint_unrendered_records(stage4: Dict,
                            blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Per-record render check over fused multi-record stage-4 entries: the
    structured-fields-only render paths keep the extracted record and drop
    the unextracted remainder lines with no bullet fallback (#221). No
    element_type filter — the KFGXBW loss was on a 'break' entry; 'T' is
    skipped (appendix catch-all)."""
    # Only h.text (the verbatim-containment haystack) is used here; h.tokens
    # (the pooled document token set) is deliberately not — this lint scores
    # each record against per-OUTPUT-LINE token sets so common academic words
    # scattered across unrelated sections can't vouch for a dropped record.
    h = _haystacks(blocks)
    line_tokens = _line_token_sets(blocks)
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code == "T":
            continue
        records = _record_lines(e.get("text"))
        if len(records) < UNRENDERED_MIN_RECORD_LINES:
            continue
        absent = [r for r in records
                  if _record_rendered(r, h.text, line_tokens) is False]
        if not absent:
            continue
        findings.append(_finding(
            "unrendered_records", "WARN",
            f"entry {e.get('element_idx_start')} ({code}): {len(absent)} of "
            f"{len(records)} records absent from output",
            [r[:100] for r in absent[:5]]))
    return findings


# -------------------------------------------------------------------- lint 9

def lint_enrichment_failures(stage5e: Dict) -> List[Dict]:
    """Publications whose stage-5 PubMed enrichment ended in a *_failed status
    (lookup_failed, pmcid_conversion_failed, doi_found_but_fetch_failed):
    their citations degrade to CV-extracted fields. Non-failure outcomes
    (enriched, no_identifier, doi_not_in_pubmed) are expected vocabulary."""
    failed = [e for e in stage5e.get("entries", [])
              if str(e.get("enrichment_status") or "").endswith("_failed")]
    if not failed:
        return []
    counts: Dict[str, int] = {}
    for e in failed:
        status = str(e.get("enrichment_status"))
        counts[status] = counts.get(status, 0) + 1
    breakdown = ", ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    return [_finding(
        "enrichment_failures", "WARN",
        f"{len(failed)} publication(s) failed PubMed enrichment ({breakdown}) "
        f"— citations degrade to CV-extracted fields (#222)",
        [str(e.get("text", ""))[:100] for e in failed[:3]])]


# ------------------------------------------------------------------- lint 10

def lint_stage6_warnings(report: Dict) -> List[Dict]:
    """Stage 6's post-generation self-check (_validate_output) findings,
    re-emitted from the render-warnings sidecar so they reach the doctor
    report and the Teams card instead of dying in the pod log (#228)."""
    findings = []
    for w in report.get("warnings", []):
        findings.append(_finding(
            "stage6_render_warnings", "WARN",
            f"stage 6 self-check: {w.get('message', '')}",
            [str(e)[:100] for e in (w.get("evidence") or [])[:3]]))
    return findings


# ------------------------------------------------------------------- lint 11

# A dropped entry this well contained (token-wise) in the kept entry is a
# true duplicate; anything below carries content the kept entry lacks. On
# 2Q1_ZQ the one true duplicate scored 1.00 and the seven real losses
# 0.60-0.89 (#227).
DEDUP_SAFE_CONTAINMENT = 0.9
_DEDUP_TOKEN_RE = re.compile(r"[a-z0-9]+")


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


# ------------------------------------------------------------------- lint 12

# One legitimate pipe can appear in a title; a cluster of single-pipe bullets
# under one section is the fused-cell fallback shape (19 under K4 on 2Q1_ZQ).
PIPE_LEAK_MIN_SEPS = 2
PIPE_CLUSTER_MIN = 3
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+\.\s")
# "...; 2025 November 20; Orlando, FL." — the venue-date wedge of one
# citation; two or more in a single numbered item means fused citations.
_VENUE_DATE_RE = re.compile(r";\s*(?:19|20)\d{2}\b[^;.\n]*;")


def lint_pipe_leaks(blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Verbatim-fallback formatting reaching the output document: paragraphs
    carrying multiple raw ' | ' field separators, clusters of single-pipe
    bullets under one section, and numbered citations fusing several
    venue-date patterns (#208 rendered costs). Paragraph blocks only:
    _table_lines synthesizes ' | ' row joins by design. The appendix is
    excluded — it is verbatim-by-contract."""
    multi: List[str] = []
    fused: List[str] = []
    clusters: Dict[str, List[str]] = {}
    section = None
    in_appendix = False
    for kind, text in blocks:
        if kind != "p":
            continue
        line = str(text).strip()
        if not line:
            continue
        if line == _APPENDIX_HEADER:
            in_appendix = True
            continue
        header = _output_section_header(line)
        if header is not None:
            section = header
            continue
        if in_appendix or is_template_instruction(line) or is_source_boilerplate(line):
            continue
        seps = line.count(" | ")
        if seps >= PIPE_LEAK_MIN_SEPS:
            multi.append(f"[{section or '?'}] {line[:100]}")
        elif seps == 1 and line.startswith("•"):
            clusters.setdefault(section or "?", []).append(line[:100])
        if (seps < PIPE_LEAK_MIN_SEPS and _NUMBERED_LINE_RE.match(line)
                and len(_VENUE_DATE_RE.findall(line)) >= 2):
            fused.append(f"[{section or '?'}] {line[:100]}")
    findings = []
    if multi:
        findings.append(_finding(
            "pipe_leaks", "WARN",
            f"{len(multi)} rendered line(s) with >={PIPE_LEAK_MIN_SEPS} "
            f"' | ' field separators — verbatim-fallback formatting reached "
            f"the output",
            multi[:5]))
    for sec, lines in clusters.items():
        if len(lines) >= PIPE_CLUSTER_MIN:
            findings.append(_finding(
                "pipe_leaks", "WARN",
                f"{len(lines)} single-pipe bullet(s) under '{sec}' — "
                f"fused-cell fallback shape",
                lines[:5]))
    if fused:
        findings.append(_finding(
            "pipe_leaks", "WARN",
            f"{len(fused)} numbered citation(s) fusing multiple venue-date "
            f"patterns",
            fused[:5]))
    return findings


# ------------------------------------------------------------------- lint 13

HONORS_NAME_BLOB_CHARS = 150
_SENTENCE_BOUNDARY_RE = re.compile(r"\.\s+[A-Z]")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC"}


def lint_table_shape(tables: List[List[List[str]]]) -> List[Dict]:
    """Honors-like tables whose rows are mis-shaped (#229): the stage-6
    multi-award fallback puts citation blobs in the name cell, leaks state
    abbreviations into the organization column, leaves the date column empty
    while the year sits in the name, and duplicates the organization inside
    the name."""
    findings = []
    for tbl in tables:
        if len(tbl) < 2 or not tbl[0]:
            continue
        header = [_norm(cell) for cell in tbl[0]]
        header_all = " ".join(header)
        if "name of award" not in header_all and "date awarded" not in header_all:
            continue

        def col(*keys):
            for idx, h in enumerate(header):
                if any(k in h for k in keys):
                    return idx
            return None

        name_i = col("award", "honor")
        org_i = col("organization", "granting")
        date_i = col("date", "yyyy", "year")
        if name_i is None:
            continue
        rows = [r for r in tbl[1:] if any(r)]
        defective_rows = set()
        defects: List[str] = []

        def flag(rn, msg):
            defective_rows.add(rn)
            defects.append(f"row {rn}: {msg}")

        for rn, row in enumerate(rows, start=1):
            name = row[name_i] if name_i < len(row) else ""
            org = row[org_i] if org_i is not None and org_i < len(row) else ""
            date = row[date_i] if date_i is not None and date_i < len(row) else ""
            if (len(name) > HONORS_NAME_BLOB_CHARS
                    or len(_SENTENCE_BOUNDARY_RE.findall(name)) >= 2):
                flag(rn, f"name-cell blob ({len(name)} chars): {name[:80]}")
            if date_i is not None and not date and _YEAR_RE.search(name):
                flag(rn, f"empty date but year in name: {name[:80]}")
            if org in _US_STATE_ABBREVS:
                flag(rn, f"organization is a bare state abbrev: '{org}'")
            elif org and len(org) > 8 and _norm(org) in _norm(name):
                flag(rn, f"organization duplicated in name: {org[:60]}")
        if defects:
            findings.append(_finding(
                "table_shape", "WARN",
                f"honors table: {len(defective_rows)}/{len(rows)} row(s) "
                f"malformed ({len(defects)} defect(s)) — #229",
                defects[:6]))
    return findings


# ------------------------------------------------------------------- lint 14

_OWNER_GATE = "HARD-FAIL gate 'CV owner name / contact populated'"
_OWNER_CAP = "the quality score is capped at 25 (RED, do not deliver)"


def lint_owner_contact_missing(stage4: Optional[Dict], uid: str,
                               unreadable: Optional[str] = None) -> List[Dict]:
    """The quality score's cap-25 hard-fail gate: the document cannot be
    delivered under anyone's name. The predicate is the scorer's own
    (quality_score.cv_owner_name_missing), applied to the stage-4 artifact the
    doctor already loads — the same ``*_fields.json`` the scorer reads.

    Accepts ``stage4=None`` rather than being skipped by ``_ready`` because
    score_cv_owner caps at 25 for an ABSENT ``*_fields.json`` too ("no
    fields.json found"); the call site decides when that case is a real run
    rather than a wrong uid.

    The evidence names which fields are populated but never their values: a
    partly-extracted owner (LLM found a surname but no given name) fires this
    gate, and the doctor report is mirrored to S3 and served by the admin
    viewer."""
    if stage4 is None:
        cause = (f"the stage-4 *_fields.json will not parse ({unreadable})"
                 if unreadable else
                 "there is no stage-4 *_fields.json for this document")
        return [_finding("owner_contact_missing", "ERROR",
                         f"{_OWNER_GATE}: {cause} — {_OWNER_CAP}")]
    if not cv_owner_name_missing(stage4):
        return []
    cv_owner = stage4.get("cv_owner", {}) or {}
    fields = ("full_name", "first_name", "last_name")
    populated = [f for f in fields if str(cv_owner.get(f) or "").strip()]
    evidence = ["cv_owner name fields populated: " + (", ".join(populated)
                                                      or "none")]
    if str(cv_owner.get("last_name") or "").strip().lower() == uid.lower():
        evidence.append(
            "last_name is the document uid — stage 4 fell back to the file "
            "stem, so the rendered document carries the uid as the owner name")
    return [_finding(
        "owner_contact_missing", "ERROR",
        f"{_OWNER_GATE}: the cv_owner block has no usable name — {_OWNER_CAP}",
        evidence)]


# ------------------------------------------------------------------- lint 15

def lint_pipeline_errors(artifacts: Dict[str, Dict]) -> List[Dict]:
    """The quality score's cap-40 hard-fail gate: an ``error`` field somewhere
    in the run's artifacts carries a fatal pattern (NameError, traceback), so a
    stage died mid-run and whatever it owned is missing from the output. The
    pattern and the walk are the scorer's own
    (quality_score.FATAL_ERROR_PATTERN / iter_error_fields).

    ``artifacts`` is keyed by stage label and the caller narrows it to exactly
    the JSON the deployed scorer reads, so the cap this finding names is the
    cap those artifacts actually produce."""
    fatal: List[str] = []
    for label in sorted(artifacts):
        for path, value in iter_error_fields(artifacts[label], label):
            if FATAL_ERROR_PATTERN.search(value):
                fatal.append(f"{path}: {value[:120]}")
    if not fatal:
        return []
    return [_finding(
        "pipeline_errors_present", "ERROR",
        f"HARD-FAIL gate 'Pipeline/API errors present': {len(fatal)} fatal "
        f"error field(s) recorded in the run artifacts — the quality score is "
        f"capped at 40 (RED, do not deliver)",
        fatal[:5])]


# --------------------------------------------------------- artifact resolution

_ARTIFACTS = {
    "stage_1a": ("stage_1a_segmentation", "_segmented.json"),
    "stage_2": ("stage_2_entry_extraction", "_entries.json"),
    "stage_3b": ("stage_3b_classified_entries", "_classified.json"),
    "stage_4": ("stage_4_field_extraction", "_fields.json"),
    "stage_5_enrichment": ("stage_5_enrichment", "_enriched.json"),
    "stage_6_docx": ("stage_6_wcm_documents", "_wcm.docx"),
    "stage_6_report": ("stage_6_wcm_documents", "_render_warnings.json"),
}

#: The owner gate reports an ABSENT *_fields.json only for a run that got as
#: far as rendering a deliverable, or whose stage-4 file exists but will not
#: parse. An earlier artifact (stage_2/stage_3b) is not enough: a run doctored
#: mid-pipeline, or one that crashed after stage 2, has no owner name YET --
#: reporting "do not deliver" on it would be a false positive (#437).
_DELIVERABLE = ("stage_4", "stage_6_docx")


def _uid_owns(name: str, uid: str) -> bool:
    """Does file ``name`` belong to ``uid`` -- and not to a longer uid that
    merely starts with it?

    ``glob(f"{uid}*")`` is a PREFIX match, so uid 'web05' also matches
    'web050_entries.json'. Sorted, '0' (0x30) sorts before '_' (0x5F), so the
    WRONG CV wins: the 2026-07-15 sweep doctored web04 against web049, web05
    against web050 and web06 against web060 -- 3 of 25 CVs diagnosed entirely
    against another CV's artifacts. Require the uid to end at a non-alphanumeric
    boundary ('web05_entries.json' yes, 'web050_entries.json' no).
    """
    if not uid or not name.startswith(uid):
        return False
    rest = name[len(uid):]
    return bool(rest) and not rest[0].isalnum()


def _find_artifact(root: Path, uid: str, key: str) -> Optional[Path]:
    stage_dir, suffix = _ARTIFACTS[key]
    directory = root / stage_dir
    if not directory.is_dir():
        return None
    matches = sorted(p for p in directory.glob(f"{uid}*{suffix}")
                     if _uid_owns(p.name, uid))
    return matches[0] if matches else None


def _find_source(root: Path, uid: str) -> Optional[Path]:
    for directory in (root, root / "uploads"):
        if directory.is_dir():
            matches = sorted(p for p in directory.glob(f"{uid}*.docx")
                             if not p.name.endswith("_wcm.docx")
                             and _uid_owns(p.name, uid))
            if matches:
                return matches[0]
    return None


def _load_json(path: Optional[Path], label: str = None,
               on_unreadable=None) -> Optional[Dict]:
    """Load an artifact JSON, or None if it is absent.

    `path` comes from _find_artifact/_find_source, which glob -- so a non-None
    path always names a file that EXISTS. A load failure on it therefore means
    present-but-unreadable (corrupt JSON, permission error), which is NOT the
    same as absent: report it via on_unreadable so a lint does not silently
    degrade to "skipped: missing <stage>". Absent (path is None) stays quiet.
    """
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("run_doctor could not read %s (%s): %s",
                       label or path, type(e).__name__, e)
        if on_unreadable is not None:
            on_unreadable(label or str(path), f"{type(e).__name__}: {e}")
        return None


def _try(fn, label: str = None, on_unreadable=None):
    """Run a docx-reading loader. Its callers guard on the path existing, so a
    failure here is also present-but-unreadable, reported like _load_json."""
    try:
        return fn()
    except Exception as e:
        logger.warning("run_doctor could not read %s (%s): %s",
                       label or "docx", type(e).__name__, e)
        if on_unreadable is not None:
            on_unreadable(label or "docx", f"{type(e).__name__}: {e}")
        return None


# --------------------------------------------------------------------- doctor

def _ready(lint_id: str, *, unreadable: Dict[str, str], findings: List[Dict],
           **inputs) -> bool:
    """True when every input for a lint is present; else record why it was
    skipped and return False.

    A None input is an ERROR when its file existed but would not parse, and the
    benign INFO "skipped: missing" when it was genuinely absent. The two are
    told apart by looking the input's NAME up in `unreadable`, so each keyword
    name passed here MUST equal the label the matching loader gave `_note`
    (e.g. missed_headers' `candidates` input is loaded with label
    "candidates"). Break that alignment and a broken artifact silently degrades
    to INFO. `unreadable`/`findings` are passed in explicitly rather than closed
    over so that coupling is visible in the signature."""
    missing = [name for name, value in inputs.items() if value is None]
    if not missing:
        return True
    broken = [name for name in missing if name in unreadable]
    absent = [name for name in missing if name not in unreadable]
    if absent:
        findings.append(_finding(
            lint_id, "INFO", "skipped: missing " + ", ".join(absent)))
    if broken:
        findings.append(_finding(
            lint_id, "ERROR", "skipped: unreadable " + ", ".join(
                f"{name} ({unreadable[name]})" for name in broken)))
    return False


def run_doctor(root: Path, uid: str, source: Optional[Path] = None) -> Dict:
    """Run every lint whose artifacts exist under root for this document uid.
    Never raises on missing/unreadable artifacts and never calls sys.exit —
    the backend calls this in-process; the CLI wraps it."""
    root = Path(root)
    paths = {key: _find_artifact(root, uid, key) for key in _ARTIFACTS}
    source_path = Path(source) if source else _find_source(root, uid)

    # Artifacts that exist but failed to load, keyed by the label their loader
    # passed to _note -- which MUST match the input kwarg name _ready() checks
    # (so a None input is traced back to a broken file vs a genuinely absent
    # one). The source docx feeds two independent readers; they take separate
    # labels so a reader that fails alone is attributed to the right lint.
    unreadable: Dict[str, str] = {}
    def _note(label, detail):
        unreadable[label] = detail

    stage_1a = _load_json(paths["stage_1a"], "stage_1a", _note)
    stage_2 = _load_json(paths["stage_2"], "stage_2", _note)
    stage_3b = _load_json(paths["stage_3b"], "stage_3b", _note)
    stage_4 = _load_json(paths["stage_4"], "stage_4", _note)
    stage_5e = _load_json(paths["stage_5_enrichment"], "stage_5_enrichment", _note)
    stage_6_report = _load_json(paths["stage_6_report"], "stage_6_report", _note)
    source_lines = _try(lambda: iter_source_lines(str(source_path)), "source", _note) if source_path else None
    candidates = _try(lambda: iter_header_candidates(str(source_path)), "candidates", _note) if source_path else None
    blocks = _try(lambda: read_docx_blocks(str(paths["stage_6_docx"])), "stage_6_docx", _note) if paths["stage_6_docx"] else None
    table_rows = _try(lambda: read_docx_table_rows(str(paths["stage_6_docx"])), "stage_6_docx", _note) if paths["stage_6_docx"] else None

    findings: List[Dict] = []
    ready = partial(_ready, unreadable=unreadable, findings=findings)

    if ready("segmentation", source=source_lines, stage_1a=stage_1a, stage_2=stage_2):
        findings.extend(lint_segmentation(source_lines, stage_1a, stage_2))
    if ready("missed_headers", candidates=candidates, stage_1a=stage_1a, stage_2=stage_2):
        findings.extend(lint_missed_headers(candidates, stage_1a, stage_2))
    if ready("bucket_status", stage_4=stage_4, stage_6_docx=blocks):
        findings.extend(lint_bucket_status(stage_4, blocks))
    if ready("under_extraction", stage_4=stage_4):
        findings.extend(lint_under_extraction(stage_4))
    if ready("classified_unrendered", stage_3b=stage_3b, stage_6_docx=blocks):
        findings.extend(lint_classified_unrendered(stage_3b, blocks))
    if ready("output_hygiene", stage_6_docx=blocks):
        findings.extend(lint_output_hygiene(blocks))
    if ready("dead_sections", stage_2=stage_2, stage_6_docx=blocks):
        findings.extend(lint_dead_sections(stage_2, blocks))
    if ready("unrendered_records", stage_4=stage_4, stage_6_docx=blocks):
        findings.extend(lint_unrendered_records(stage_4, blocks))
    if ready("enrichment_failures", stage_5_enrichment=stage_5e):
        findings.extend(lint_enrichment_failures(stage_5e))
    if ready("stage6_render_warnings", stage_6_report=stage_6_report):
        findings.extend(lint_stage6_warnings(stage_6_report))
    if ready("dedup_drops", stage_6_report=stage_6_report):
        findings.extend(lint_dedup_drops(stage_6_report))
    if ready("pipe_leaks", stage_6_docx=blocks):
        findings.extend(lint_pipe_leaks(blocks))
    if ready("table_shape", stage_6_docx=table_rows):
        findings.extend(lint_table_shape(table_rows))
    # score_cv_owner caps at 25 for an ABSENT *_fields.json as well as an empty
    # cv_owner name, so this lint breaks the house "missing artifact -> skip"
    # convention: skipping the absent case would report the more broken run
    # more quietly (#437). It still skips for a run that never reached stage 4
    # -- an incomplete or wrong-uid run has no owner name yet, and the batch
    # runner doctors CVs whose pipeline returned rc!=0.
    if stage_4 is not None or any(paths[k] for k in _DELIVERABLE):
        findings.extend(lint_owner_contact_missing(
            stage_4, uid, unreadable.get("stage_4")))
    else:
        ready("owner_contact_missing", stage_4=stage_4)
    # The error scan covers exactly the JSON the DEPLOYED scorer globs:
    # quality_score_service copies *_entries/_classified/_fields.json into the
    # dir it scores, which are stage_2/stage_3b/stage_4 here. It scans whatever
    # subset of those loaded rather than requiring all three -- the scorer
    # scans whatever landed too, so an absent artifact must not hide a fatal
    # recorded in another. `ready` still reports the skip when none loaded, so
    # absent stays INFO and unreadable stays ERROR under real loader labels.
    scored_artifacts = {label: data for label, data in (
        ("stage_2", stage_2), ("stage_3b", stage_3b), ("stage_4", stage_4))
        if data is not None}
    if scored_artifacts:
        findings.extend(lint_pipeline_errors(scored_artifacts))
    else:
        ready("pipeline_errors_present", stage_2=stage_2, stage_3b=stage_3b,
              stage_4=stage_4)

    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for f in findings:
        counts[f["severity"]] += 1
    worst = next((s for s in SEVERITY_ORDER if counts[s]), None)

    return {
        "document_uid": uid,
        "root": str(root),
        "artifacts": {
            "source": str(source_path) if source_path else None,
            **{key: str(p) if p else None for key, p in paths.items()},
        },
        "findings": findings,
        "counts": counts,
        "worst_severity": worst,
    }


def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", help="run outputs root (contains the stage_* dirs)")
    parser.add_argument("uid", help="document uid (artifact filename prefix)")
    parser.add_argument("--source", help="source .docx (default: <root>[/uploads]/<uid>*.docx)")
    parser.add_argument("--out", help="report file (default: <root>/<uid>_doctor.json)")
    args = parser.parse_args(argv)

    # Advisory pre-check only: fail fast with a clear message BEFORE the slow
    # docx parsing if the target is already unwritable. It is NOT the
    # correctness guarantee -- the file can still turn unwritable between here
    # and the write (TOCTOU), so the write below is wrapped in its own guard.
    out_path = Path(args.out) if args.out else Path(args.root) / f"{args.uid}_doctor.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not os.access(out_path, os.W_OK):
        parser.error(f"output path not writable: {out_path}")

    payload = run_doctor(Path(args.root), args.uid,
                         Path(args.source) if args.source else None)

    report_json = json.dumps(payload, indent=2)
    if len(report_json.encode("utf-8")) > MAX_REPORT_BYTES:
        logger.warning("doctor report exceeds %d bytes; truncating to %d "
                       "findings", MAX_REPORT_BYTES, MAX_REPORT_FINDINGS)
        payload["findings"] = payload["findings"][:MAX_REPORT_FINDINGS]
        payload["findings_truncated"] = True
        report_json = json.dumps(payload, indent=2)
    try:
        out_path.write_text(report_json, encoding="utf-8")
    except OSError as e:
        print(f"error: could not write report to {out_path}: {e}",
              file=sys.stderr)
        sys.exit(2)

    print(f"run doctor: {args.uid}")
    for f in payload["findings"]:
        print(f"  [{f['severity']}] {f['lint']}: {f['message']}")
        for line in f["evidence"][:3]:
            print(f"      - {line}")
    counts = payload["counts"]
    print(f"\n{counts['ERROR']} error(s), {counts['WARN']} warning(s), "
          f"{counts['INFO']} info -> {out_path}")
    sys.exit(1 if counts["ERROR"] or counts["WARN"] else 0)


if __name__ == "__main__":
    main()
