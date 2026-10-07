"""The finished document with the run doctor's findings as Word comments (#1388 C).

A reviewer sees each WARN or ERROR finding at the spot it fires, in the file
they are already editing. The comment says what the run page says: the lint's
title, the instance's detail and the lint's what-to-do (run_quality_report).
The clean document is left as it is; this writes a copy beside it.
"""
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from app.services.artifact_service import REVIEW_DOCX_SUFFIX
from app.services.run_quality_report import (
    LINT_COPY,
    MAX_INSTANCES_SHOWN,
    TRUNCATION_MARK,
    _instance,
    _usable_findings,
)
from unified_pipeline.core.text_norm import squash  # noqa: E402
from unified_pipeline.stage4.schemas import TAXONOMY_LABELS  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

#: What reading or writing a docx raises: quality_score's ``_load_docx`` set,
#: plus PackageNotFoundError, which python-docx raises for a file that is not a zip.
REVIEW_DOCX_ERRORS = (OSError, zipfile.BadZipFile, KeyError, ValueError, XMLSyntaxError,
                      PackageNotFoundError)
COMMENT_AUTHOR = "CViche check"
COMMENT_INITIALS = "CV"
COMMENTED_SEVERITIES = ("ERROR", "WARN")
#: Said in a comment the document's first paragraph carries for want of a place.
UNLOCATED_NOTE = "Not tied to one place in the document: see the run page for the details."
#: Squashed characters of a quote matched against the output. Long enough that
#: a hit is that passage, short enough to survive the doctor's own cuts.
QUOTE_PROBE_CHARS = 40
#: Shorter quotes ("PI", a year) would anchor on the first paragraph holding them.
MIN_QUOTE_CHARS = 12
#: Squashed length past which a paragraph is a record, not a heading.
MAX_HEADING_CHARS = 100
#: The doctor's locator before output text in a note: "block 337 repeats at 338: ".
_NOTE_LOCATOR_RE = re.compile(r"^(?:entry|row|blocks?|element_idx_start|stage) \d[^:]{0,40}:\s*")
_CODE_BY_LABEL = {label: code for code, label in TAXONOMY_LABELS.items()}
#: Top-level template headings for the codes stage 6's heading map leaves blank
#: (it routes no overflow there). Substrings, matched as stage 6 matches its own.
_TOP_LEVEL_HEADINGS = {
    "A": "PERSONAL DATA", "B": "EDUCATION", "C": "POSTDOCTORAL",
    "D": "PROFESSIONAL POSITIONS", "E": "EMPLOYMENT STATUS", "F": "LICENSURE",
    "G": "INSTITUTIONAL/HOSPITAL", "I": "PROFESSIONAL ORGANIZATIONS",
    "J": "PERCENT EFFORT", "T": "APPENDIX",
}


def review_docx_path(clean_docx: Path) -> Path:
    """Where the annotated copy of ``<uid>_wcm.docx`` is written."""
    return clean_docx.with_name(clean_docx.name.removesuffix("_wcm.docx") + REVIEW_DOCX_SUFFIX)


def _paragraph_text(p: Paragraph) -> str:
    # ponytail: w:t only, so a tracked insertion (enrichment) counts and a
    # tracked deletion (w:delText) does not; Paragraph.text skips w:ins runs.
    return "".join(t.text or "" for t in p._p.iter(qn("w:t")))


def _comment_text(finding: dict, located: bool) -> str:
    inst = _instance(finding)
    copy = LINT_COPY.get(finding["lint"])
    lines = [f"{copy.title if copy else finding['lint']}: {inst.detail}"]
    if not located:
        lines.append(UNLOCATED_NOTE)
    if inst.section:
        lines.append(f"Section: {inst.section}")
    if copy:
        lines.append(f"What to do: {copy.what_to_do}")
    return "\n".join(lines)


def _probes(text: str) -> list[str]:
    """Squashed windows of a quote: its start, middle and end, since stage 6
    reformats the source text a quote was taken from (labels, date forms)."""
    s = squash(_NOTE_LOCATOR_RE.sub("", text).removesuffix(TRUNCATION_MARK))
    if len(s) < MIN_QUOTE_CHARS:
        return []
    starts = {0, max(0, len(s) // 2 - QUOTE_PROBE_CHARS // 2), max(0, len(s) - QUOTE_PROBE_CHARS)}
    return [s[i:i + QUOTE_PROBE_CHARS] for i in sorted(starts)]


def _heading_text(section: str) -> str:
    """The template heading text stage 6 files this section's records under."""
    code = _CODE_BY_LABEL.get(section, "")
    return (WCMTemplateGenerator._get_wcm_section_header(code)
            or _TOP_LEVEL_HEADINGS.get(code[:1], ""))


def _anchor(finding: dict, paragraphs: list[tuple[Paragraph, str]]) -> Paragraph | None:
    """The first paragraph holding one of the finding's quotes (or the output
    text its notes carry); else its section's heading; else None."""
    inst = _instance(finding)
    for text in [*inst.quotes, *inst.notes]:
        for n, probe in enumerate(_probes(text)):
            hits = [p for p, body in paragraphs if probe in body]
            # A quote's start is its own (a repeated record's first copy is
            # the right spot); a title or department in its middle or end
            # recurs across records, so it only counts when it is unique.
            if hits and (n == 0 or len(hits) == 1):
                return hits[0]
    heading = squash(_heading_text(inst.section)) if inst.section else ""
    if heading:
        # Stage 6's own rule (_find_paragraph_with_text): the first paragraph
        # containing the heading text, kept to heading-length paragraphs.
        hit = next((p for p, body in paragraphs
                    if heading in body and len(body) <= MAX_HEADING_CHARS), None)
        if hit is not None:
            return hit
    return None


def write_review_docx(clean_docx: Path, doctor_payload: object) -> tuple[Path, int] | None:
    """Write the annotated copy of ``clean_docx``; return its path and how
    many comments it carries. None when there is nothing to comment on."""
    if not isinstance(doctor_payload, dict):
        return None
    findings = [f for f in _usable_findings(doctor_payload)[0]
                if f["severity"] in COMMENTED_SEVERITIES]
    per_lint: dict[str, int] = {}
    shown = []
    for f in findings:  # report order, at most as many per lint as the run page lists
        per_lint[f["lint"]] = per_lint.get(f["lint"], 0) + 1
        if per_lint[f["lint"]] <= MAX_INSTANCES_SHOWN:
            shown.append(f)
    if not shown:
        return None

    doc = Document(str(clean_docx))
    paragraphs = [(p, squash(_paragraph_text(p)))
                  for p in (Paragraph(el, doc._body) for el in doc.element.body.iter(qn("w:p")))]
    if not paragraphs:
        return None
    for f in shown:
        para = _anchor(f, paragraphs)
        located = para is not None
        para = para if located else paragraphs[0][0]
        runs = para.runs or [para.add_run()]
        doc.add_comment(runs, text=_comment_text(f, located), author=COMMENT_AUTHOR,
                        initials=COMMENT_INITIALS)
    out = review_docx_path(clean_docx)
    doc.save(str(out))
    return out, len(shown)
