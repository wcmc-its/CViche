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
14. duplicate_passages    stretches of 3+ CONSECUTIVE rendered blocks that
                          appear twice in the output document — one record
                          reaching the faculty-facing docx more than once
                          (#439: C0ZGFW rendered whole teaching records twice)

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
import math
import os
import re
import sys
from functools import partial
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from unified_pipeline.core.template_boilerplate import is_source_boilerplate
from unified_pipeline.segmentation_regression import iter_source_lines

# Lint rules and their primitives now live in the doctor/ package (#493).
# Re-exported here rather than updating callers: five files import 33 names
# from this module, including the backend orchestrator, and a moved address
# that is not re-exported fails at IMPORT time -- which reads as a lost fix.
# test_run_doctor_contract.py pins that surface.
from unified_pipeline.doctor.shared import (  # noqa: F401,E402
    Haystack,
    RENDER_PIECE_MIN_CHARS,
    RENDER_PIECE_WINDOW,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _LINE_SENTINEL,
    _RENDER_TOKEN_RE,
    _SECTION_HEADER_RE,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
)
from unified_pipeline.doctor.lints.extraction import (  # noqa: F401,E402
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    DEDUP_SAFE_CONTAINMENT,
    UNDER_EXTRACTION_MAX_PCT,
    UNDER_EXTRACTION_MIN_CHARS,
    UNDER_EXTRACTION_MIN_RECORDS,
    _DEDUP_TOKEN_RE,
    _FUNDING_SECTIONS,
    _STATUS_LABEL_RE,
    _YEAR_EDGE_LINE_RE,
    _alphanumeric_tokens,
    _entry_rendered,
    _entry_status,
    _funding_haystacks,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dedup_drops,
    lint_taxonomy_code_coverage,
    lint_under_extraction,
)
from unified_pipeline.doctor.lints.enrichment import (  # noqa: F401,E402
    _OWNER_CAP,
    _OWNER_GATE,
    lint_enrichment_failures,
    lint_owner_contact_missing,
)
from unified_pipeline.doctor.lints.render import (  # noqa: F401,E402
    APPENDIX_WARN_ENTRIES,
    DEAD_SECTION_MIN_LINES,
    DUPLICATE_PASSAGE_MIN_BLOCKS,
    DUPLICATE_PASSAGE_WARN_COUNT,
    HONORS_NAME_BLOB_CHARS,
    PIPE_CLUSTER_MIN,
    PIPE_LEAK_MIN_SEPS,
    RECORD_DATE_LINE_MIN_CHARS,
    TABLE_SHAPE_WARN_DEFECTS,
    TABLE_SHAPE_WARN_ROW_RATIO,
    UNRENDERED_MIN_RECORD_LINES,
    _APPENDIX_ENTRY_RE,
    _APPENDIX_HEADER,
    _BRACKET_CODE_RE,
    _NUMBERED_LINE_RE,
    _PASSAGE_ENUMERATOR_RE,
    _PASSAGE_PUNCT_RE,
    _RECORD_DATE_PREFIX_RE,
    _SENTENCE_BOUNDARY_RE,
    _US_STATE_ABBREVS,
    _VENUE_DATE_RE,
    _YEAR_RE,
    _is_appendix_noise,
    _line_token_sets,
    _names_match,
    _passage_key,
    _record_lines,
    _record_rendered,
    lint_dead_sections,
    lint_duplicate_passages,
    lint_output_hygiene,
    lint_pipe_leaks,
    lint_stage6_warnings,
    lint_table_shape,
    lint_unrendered_records,
)
from unified_pipeline.doctor.lints.segmentation import (  # noqa: F401,E402
    MISSED_HEADERS_WARN_COUNT,
    _header_key,
    _hierarchy_titles,
    lint_missed_headers,
    lint_segmentation,
)
from unified_pipeline.doctor.lints.runtime import (  # noqa: F401,E402
    lint_pipeline_errors,
)


logger = logging.getLogger(__name__)


SEVERITY_ORDER = ("ERROR", "WARN", "INFO")  # most to least severe


# How often each lint fires at all, over the same 73 scored runs. Used only to
# ORDER what gets shown, never to decide severity. `top_lints` used to be
# `most_common(4)`, which ranks by raw finding count -- so output_hygiene
# (87.7% of runs) and table_shape (56.2%) consumed half the slots in every
# report and pushed the 1-3% lints, the ones that actually distinguish this run
# from every other run, out of view (#438).
#
# Same staleness caveat as the thresholds above: #440 replaces this with a live
# baseline. A lint absent from this table is treated as maximally surprising,
# which is the safe direction -- a newly added lint surfaces rather than hides.
#: Every lint key `run_doctor` can emit, in the order the lints run (#268).
#:
#: This is the canonical list. Consumers must import it rather than hardcoding
#: their own copy -- `scripts/corpus_doctor_sweep.py` kept one and it drifted.
#:
#: It cannot be derived from the `lint_*` function names, and that is not a
#: style preference. Two functions emit a key that is not their own name:
#:
#:     lint_stage6_warnings  -> "stage6_render_warnings"
#:     lint_pipeline_errors  -> "pipeline_errors_present"
#:
#: A name-derived list would invent two keys no finding ever carries (so they
#: report as "ran on 0 CVs") and drop the two that are real. A name-derived
#: COUNT is wrong for a third reason: `lint_surprise` matches the `lint_`
#: prefix but is a ranking helper, not a rule, and emits no findings at all.
#:
#: Order is load-bearing. The sweep breaks ranking ties on index, so reordering
#: this changes its report even when every finding is identical. Keep it in
#: dispatch order, matching `run_doctor()`.
#:
#: `test_run_doctor_contract.py` checks this against the keys the lint
#: bodies actually pass to `_finding`/`_ready`, so adding a lint without
#: registering it here fails in CI.
KNOWN_LINTS = (
    "segmentation",
    "missed_headers",
    "bucket_status",
    "under_extraction",
    "classified_unrendered",
    "taxonomy_code_coverage",
    "output_hygiene",
    "dead_sections",
    "unrendered_records",
    "enrichment_failures",
    "stage6_render_warnings",
    "dedup_drops",
    "pipe_leaks",
    "table_shape",
    "duplicate_passages",
    "owner_contact_missing",
    "pipeline_errors_present",
)


LINT_PREVALENCE = {
    "output_hygiene": 0.877,
    "table_shape": 0.562,
    "missed_headers": 0.288,
    "classified_unrendered": 0.288,
    "stage6_render_warnings": 0.123,
    "dedup_drops": 0.110,
    "segmentation": 0.082,
    "enrichment_failures": 0.082,
    "owner_contact_missing": 0.068,
    "pipe_leaks": 0.055,
    "unrendered_records": 0.027,
    "dead_sections": 0.027,
    "duplicate_passages": 0.014,
    "bucket_status": 0.014,
    "under_extraction": 0.014,
    "pipeline_errors_present": 0.001,
}


def lint_surprise(lint: str) -> float:
    """How informative it is that THIS lint fired, in bits.

    A lint that fires on 88% of runs says almost nothing about the run in front
    of you; one that fires on 1.4% says a great deal. Ranking by raw count gets
    this exactly backwards, because the ubiquitous lints are also the ones that
    fire many times."""
    return math.log2(1.0 / max(LINT_PREVALENCE.get(lint, 0.001), 0.001))


def rank_lints(counts: Dict[str, int]) -> List[Tuple[str, int]]:
    """Order lints for display: most surprising first, count as the tiebreak."""
    return sorted(counts.items(),
                  key=lambda kv: (-lint_surprise(kv[0]), -kv[1], kv[0]))


# A well-formed report is a few KB. A malformed artifact with huge text fields
# could inflate the findings into a multi-MB file; cap it. Evidence strings are
# already sliced at construction, so the only way to blow the cap is a very
# large NUMBER of findings — truncating the list is the effective lever.
MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_REPORT_FINDINGS = 1000


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


# --------------------------------------------------------- artifact resolution

class ArtifactSpec(NamedTuple):
    stage_dir: str
    suffix: str

_ARTIFACTS = {
    "stage_1a": ArtifactSpec("stage_1a_segmentation", "_segmented.json"),
    "stage_2": ArtifactSpec("stage_2_entry_extraction", "_entries.json"),
    "stage_3b": ArtifactSpec("stage_3b_classified_entries", "_classified.json"),
    "stage_4": ArtifactSpec("stage_4_field_extraction", "_fields.json"),
    "stage_5_enrichment": ArtifactSpec("stage_5_enrichment", "_enriched.json"),
    "stage_6_docx": ArtifactSpec("stage_6_wcm_documents", "_wcm.docx"),
    "stage_6_report": ArtifactSpec("stage_6_wcm_documents", "_render_warnings.json"),
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
    spec = _ARTIFACTS[key]
    directory = root / spec.stage_dir
    if not directory.is_dir():
        return None
    matches = sorted(p for p in directory.glob(f"{uid}*{spec.suffix}")
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
    if ready("taxonomy_code_coverage", stage_3b=stage_3b):
        findings.extend(lint_taxonomy_code_coverage(stage_3b))
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
    if ready("duplicate_passages", stage_6_docx=blocks):
        findings.extend(lint_duplicate_passages(blocks))
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
