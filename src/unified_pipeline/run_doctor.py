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
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
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

# Lint 4: an entry this big, with this many record-like lines, extracting
# under this coverage is a mass-loss smell, not LLM wobble.
UNDER_EXTRACTION_MAX_PCT = 40.0
UNDER_EXTRACTION_MIN_CHARS = 800
UNDER_EXTRACTION_MIN_RECORDS = 2

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

# Lint 6: an appendix bigger than this means mapping failed at scale.
APPENDIX_WARN_ENTRIES = 15

# Lint 7: a source section with at least this many substantive lines whose
# output section is empty did not just "have nothing to say".
DEAD_SECTION_MIN_LINES = 3

_SEVERITIES = ("ERROR", "WARN", "INFO")

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

def iter_header_candidates(docx_path: str) -> List[str]:
    """Header-looking source lines: short, letters-only, ALL-CAPS bold (or
    styled as a Heading), from top-level paragraphs and single-column table
    cells (the 1x1 layout tables CVs use as section containers). Multi-column
    tables are data tables — their bold cells are column headers — and
    document furniture ('CURRICULUM VITAE', revision stamps) is not a header
    either. These are what stage 1a should have promoted to hierarchy nodes."""
    from docx import Document  # local import: doctor is optional tooling

    candidates: List[str] = []

    def consider(para):
        text = para.text.strip()
        if not 3 <= len(text) <= 60:
            return
        if not any(ch.isalpha() for ch in text) or any(ch.isdigit() for ch in text):
            return
        if is_source_boilerplate(text):
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


def read_docx_blocks(docx_path: str) -> List[Tuple[str, str]]:
    """Body-order blocks of a docx: ("p", text) per paragraph, ("table",
    cell texts joined by newlines) per table. Grants render as one Word table
    per grant, so any output check must read tables AND paragraphs."""
    from docx import Document  # local import: doctor is optional tooling
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(docx_path)
    blocks: List[Tuple[str, str]] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            blocks.append(("p", Paragraph(child, doc).text))
        elif child.tag == qn("w:tbl"):
            cell_texts = []
            for row in Table(child, doc).rows:
                for cell in row.cells:
                    if cell.text.strip():
                        cell_texts.append(cell.text)
            blocks.append(("table", "\n".join(cell_texts)))
    return blocks


def _haystacks(blocks: List[Tuple[str, str]]) -> Tuple[str, set]:
    """(squashed containment haystack, distinctive-token set) for the output
    blocks; the \\x00 sentinel keeps a piece from matching across two
    unrelated lines."""
    pieces: List[str] = []
    tokens: set = set()
    for _, text in blocks:
        for line in str(text).split("\n"):
            if line.strip():
                pieces.append(_squash(line))
                tokens.update(_RENDER_TOKEN_RE.findall(_norm(line)))
    return "\x00".join(pieces), tokens


# -------------------------------------------------------------------- lint 1

def lint_segmentation(source_lines: List[str], stage1a: Dict,
                      stage2: Dict) -> List[Dict]:
    """Coverage / lost lines / mega-entries / dups / empties, reusing the
    segmentation_regression metrics (source docx + stage 1a + stage 2)."""
    metrics = compute_metrics(source_lines, stage1a, stage2)
    findings = []
    for flag in lint_metrics(metrics):
        evidence = ([l[:100] for l in metrics["lost_lines"][:5]]
                    if flag.startswith("coverage") else [])
        findings.append(_finding("segmentation", "WARN", flag, evidence))
    return findings


# -------------------------------------------------------------------- lint 2

def lint_missed_headers(candidates: List[str], stage1a: Dict,
                        stage2: Dict) -> List[Dict]:
    """Header-looking source lines absent from the 1a hierarchy AND from
    every entry hierarchy path: a header demoted to content misroutes
    everything filed under it."""
    known = set(_hierarchy_titles(stage1a))
    paths = {_norm(h) for e in stage2.get("entries", [])
             for h in (e.get("hierarchy") or [])}
    findings, seen = [], set()
    for cand in candidates:
        normed = _norm(cand)
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


def _funding_haystacks(blocks: List[Tuple[str, str]]) -> Dict[str, Tuple[str, set]]:
    """Per-bucket (haystack, token set) of everything rendered under each of
    stage 6's funding subsection headers."""
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
        verdicts = {bucket: _entry_rendered(e.get("text"), haystack, tokens)
                    for bucket, (haystack, tokens) in rendered.items()}
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
        records = sum(1 for line in text.split("\n") if _looks_like_record(line))
        if records < UNDER_EXTRACTION_MIN_RECORDS:
            continue
        findings.append(_finding(
            "under_extraction", "WARN",
            f"entry {e.get('element_idx_start')}: extraction coverage {pct}% "
            f"on a {len(text)}-char entry with {records} record-like lines",
            [text[:120]]))
    return findings


# -------------------------------------------------------------------- lint 5

def _entry_fragments(text) -> List[str]:
    """An entry's fragments: per line, per '|' cell, and per tab cell."""
    return [frag for line in str(text or "").split("\n")
            for cell in line.split("|") for frag in cell.split("\t")]


def _entry_pieces(text) -> List[str]:
    """Squashed fragments of an entry long enough to be looked up in the
    output haystack."""
    pieces = []
    for frag in _entry_fragments(text):
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
    for chunk in [str(text or "")] + _entry_fragments(text):
        tokens = set(_RENDER_TOKEN_RE.findall(_norm(chunk)))
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
    haystack, haystack_tokens = _haystacks(blocks)
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
        verdicts = [(_entry_rendered(e.get("text"), haystack, haystack_tokens), e)
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
            [l[:100] for l in leaks[:5]]))

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


# --------------------------------------------------------- artifact resolution

_ARTIFACTS = {
    "stage_1a": ("stage_1a_segmentation", "_segmented.json"),
    "stage_2": ("stage_2_entry_extraction", "_entries.json"),
    "stage_3b": ("stage_3b_classified_entries", "_classified.json"),
    "stage_4": ("stage_4_field_extraction", "_fields.json"),
    "stage_6_docx": ("stage_6_wcm_documents", "_wcm.docx"),
}


def _find_artifact(root: Path, uid: str, key: str) -> Optional[Path]:
    stage_dir, suffix = _ARTIFACTS[key]
    directory = root / stage_dir
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"{uid}*{suffix}"))
    return matches[0] if matches else None


def _find_source(root: Path, uid: str) -> Optional[Path]:
    for directory in (root, root / "uploads"):
        if directory.is_dir():
            matches = sorted(p for p in directory.glob(f"{uid}*.docx")
                             if not p.name.endswith("_wcm.docx"))
            if matches:
                return matches[0]
    return None


def _load_json(path: Optional[Path]) -> Optional[Dict]:
    if not path:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


# --------------------------------------------------------------------- doctor

def run_doctor(root: Path, uid: str, source: Optional[Path] = None) -> Dict:
    """Run every lint whose artifacts exist under root for this document uid.
    Never raises on missing/unreadable artifacts and never calls sys.exit —
    the backend calls this in-process; the CLI wraps it."""
    root = Path(root)
    paths = {key: _find_artifact(root, uid, key) for key in _ARTIFACTS}
    source_path = Path(source) if source else _find_source(root, uid)

    stage_1a = _load_json(paths["stage_1a"])
    stage_2 = _load_json(paths["stage_2"])
    stage_3b = _load_json(paths["stage_3b"])
    stage_4 = _load_json(paths["stage_4"])
    source_lines = _try(lambda: iter_source_lines(str(source_path))) if source_path else None
    candidates = _try(lambda: iter_header_candidates(str(source_path))) if source_path else None
    blocks = _try(lambda: read_docx_blocks(str(paths["stage_6_docx"]))) if paths["stage_6_docx"] else None

    findings: List[Dict] = []

    def ready(lint_id: str, **inputs) -> bool:
        missing = [name for name, value in inputs.items() if value is None]
        if missing:
            findings.append(_finding(
                lint_id, "INFO", "skipped: missing " + ", ".join(missing)))
            return False
        return True

    if ready("segmentation", source=source_lines, stage_1a=stage_1a, stage_2=stage_2):
        findings.extend(lint_segmentation(source_lines, stage_1a, stage_2))
    if ready("missed_headers", source=candidates, stage_1a=stage_1a, stage_2=stage_2):
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

    counts = {severity: 0 for severity in _SEVERITIES}
    for f in findings:
        counts[f["severity"]] += 1
    worst = next((s for s in _SEVERITIES if counts[s]), None)

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

    payload = run_doctor(Path(args.root), args.uid,
                         Path(args.source) if args.source else None)

    out_path = Path(args.out) if args.out else Path(args.root) / f"{args.uid}_doctor.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))

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
