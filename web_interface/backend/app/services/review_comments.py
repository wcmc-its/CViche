"""The finished document with the run doctor's findings flagged in place (#1388 C).

A reviewer sees each WARN or ERROR finding as a short Word comment on the text
it is about: the year that looks wrong, the second copy of a duplicate, each
Appendix group. A finding with no one place in the document (a citation
PubMed could not match, a step that fell back) is a bullet in the review
notes that close the Appendix. The clean document is left as it is; this
writes a copy beside it.
"""
import copy
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.shared import Pt
from docx.styles.style import BaseStyle
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml.etree import XMLSyntaxError

from app.schemas import DoctorFindingInstance
from app.services.artifact_service import REVIEW_DOCX_SUFFIX
from app.services.run_quality_report import (
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
#: Flags one lint may put in one document. A lint can fire hundreds of times.
MAX_FLAGS_PER_LINT = 25

#: What a reviewer reads, on the text it is about. Short, and an instruction
#: where there is one. Every lint the run page words (LINT_COPY) has one.
REVIEW_FLAGS = {
    "segmentation": "Text from the original CV may be missing or merged here.",
    "missed_headers": "This was a section heading in the original CV.",
    "bucket_status": "Grant is under the wrong funding heading.",
    "under_extraction": "Much of this entry was not read. Check it against the original CV.",
    "classified_unrendered": "Entries from the original CV are missing here.",
    "taxonomy_code_coverage": "Sent to the Appendix: move it to the right section.",
    "stage3b_fallback_ratio": "Some entries may be in the wrong section.",
    "output_hygiene": "Stray text: delete it.",
    "dead_sections": "This section is empty, but the original CV has entries for it.",
    "unrendered_records": "Part of this entry is missing.",
    "section_lost": "Entries from the original CV are missing from this section.",
    "enrichment_failures": "Not found in PubMed: check this citation.",
    "stage6_render_warnings": "Check this section.",
    "dedup_drops": "An entry was removed as a near-duplicate.",
    "pipe_leaks": "Citations run together: split them.",
    "table_shape": "One award split across rows: merge them.",
    "duplicate_passages": "Repeated: delete this copy.",
    "duplicate_records": "Duplicate: delete this copy.",
    "protected_data_in_output": "Protected personal data: delete it.",
    "invented_records": "Not in the original CV: check it or delete it.",
    "wrong_start_date": "Shows Present, but the original has an end date.",
    "table_lost": "A table from the original CV is mostly missing.",
    "date_only_lines": "A date on its own line: join it to its entry.",
    "stage3b_second_pass_error": "Some entries may be in the wrong section.",
    "offschema_fields": "Entries from the original CV are missing from this section.",
    "implausible_year": "Year looks wrong.",
    "stage4_group_failures": "Some entries may be missing details.",
    "python_repr_in_output": "Raw data code: replace it with plain text.",
    "llm_refusal_in_output": "Not CV text: delete it.",
    "llm_fallback_served": "A backup AI model wrote part of this document: check it.",
    "stage_failure_recorded": "A processing step failed: content may be missing.",
    "owner_missing_from_citation": "The CV owner's name is missing from these authors.",
    "etal_added": 'Authors cut to "et al.": restore the full list.',
    "multi_record_coverage": "Several entries merged into one: split them.",
    "year_not_in_source": "This year is not in the original CV.",
    "date_cell_shape": "Date looks wrong.",
    "junk_or_header_row": "A heading printed as an entry: delete it.",
    "teaching_postcheck": "Reworded wrongly: check it against the original CV.",
    "contact_slot_lost": "Office contact details are missing.",
    "pubmed_title_truncated": "Title cut short: complete it.",
    "enrichment_pubtype_mismatch": "Replaced by a correction notice: restore the original citation.",
    "section_consistency": "Probably in the wrong section.",
    "segmentation_collapse": "Sections of the original CV were not found: content may be misplaced.",
    "owner_contact_missing": "The CV owner's name was not found.",
    "pipeline_errors_present": "Processing error: content may be missing.",
    "no_output": "No document was produced.",
    "grant_boundary": "These details belong to the grant before or after.",
    "grant_bucket": "Grant is under the wrong funding heading.",
    "research_summary_call_failed": "The research summary is missing.",
    "span_count": "Separate years shown as one range.",
    "role_consistency": "Wrong principal investigator or role.",
    "fanout_cell_residue": "Leftover text: delete it.",
    "identical_rendered_rows": "Same as another row: delete one.",
    "split_child_unsourced": "Place or dates come from another entry.",
    "record_boundary": "This line belongs to the entry before.",
    "citation_field_dropped": 'Title, link or "..." missing from this citation.',
    "group_header_context": "Lost the heading it sat under.",
    "orphaned_fragments": "Text from the original CV may be missing from this entry.",
    "stage4_unplaced_items": "Records from the original CV are missing from this section.",
}
#: The lint stage 6's Appendix diversions arrive under (lint_stage6_warnings).
DIVERSION_LINT = "stage6_render_warnings"
#: A dedup drop names the entry it removed; on the entry it kept, or as a review note.
DEDUP_DROPPED_FLAG = "Removed as a near-duplicate; add it back if it is a separate entry"
#: Lints whose every quoted item is its own problem, each flagged where it is.
EACH_ITEM_LINTS = frozenset({
    "duplicate_records", "duplicate_passages", "pipe_leaks", "enrichment_failures",
    "stage6_render_warnings", "dedup_drops",
})
#: Lints that quote a record printed twice: the later copy is the one to delete.
REPEAT_LINTS = frozenset({"duplicate_records", "duplicate_passages"})
#: Lints whose problem is one year their detail names ("end_date=1912").
YEAR_LINTS = frozenset({"implausible_year", "year_not_in_source"})
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
#: dedup_drops evidence: "K1 (jaccard=1.00, 89% covered by kept): dropped '<x>' vs kept '<y>'".
_DEDUP_RE = re.compile(r"dropped '(?P<dropped>.*)' vs kept '(?P<kept>.*)'$")
#: stage 6's appendix_diversion messages, after the code prefix the run page
#: strips: "2 entries diverted to the Appendix ...", "1 entry ... recovered into the Appendix".
_DIVERTED_RE = re.compile(r"(?P<count>\d+) entr(?:y|ies)\b[^.]*?(?:diverted to|recovered into) the Appendix")
#: Closes the review copy: findings with no one place in the document, one bullet each.
REVIEW_NOTES_HEADING = "Review notes from CViche (not part of the CV: delete before sending)"
NOTE_BULLET = "\u2022 "
NOTES_FONT = "Arial"
NOTES_HEADING_STYLE = "Heading 2"
NOTES_HEADING_SIZE = Pt(13)
APPENDIX_LINE_FLAG = "Not filed under any section: move it to the right one or delete it."
APPENDIX_GROUP_FLAG = "Not filed under any section: move these entries to the right one or delete them."
#: Stage 6 opens each group of Appendix lines with 'From "<source heading>":'
#: (stage6/sections/appendix.py, _write_appendix_group); one flag per group.
APPENDIX_GROUP_PREFIX = 'From "'

#: Squashed characters of a quote matched against the output. Long enough that
#: a hit is that passage, short enough to survive the doctor's own cuts.
QUOTE_PROBE_CHARS = 40
#: Shorter quotes ("PI", a year) would anchor on the first paragraph holding them.
MIN_QUOTE_CHARS = 12
#: Squashed length past which a paragraph is a record, not a heading.
MAX_HEADING_CHARS = 100
#: The doctor's locator before output text in a note: "block 337 repeats at 338: ".
_NOTE_LOCATOR_RE = re.compile(r"^(?:entry|row|blocks?|element_idx_start|stage) \d[^:]{0,40}:\s*")
#: pipe_leaks evidence names its section in brackets: "[bibliography] 93. ...".
_BRACKET_LOCATOR_RE = re.compile(r"^\[[^\]]{1,40}\]\s*")
_CODE_BY_LABEL = {label: code for code, label in TAXONOMY_LABELS.items()}
#: Top-level template headings for the codes stage 6's heading map leaves blank
#: (it routes no overflow there). Substrings, matched as stage 6 matches its own.
_TOP_LEVEL_HEADINGS = {
    "A": "PERSONAL DATA", "B": "EDUCATION", "C": "POSTDOCTORAL",
    "D": "PROFESSIONAL POSITIONS", "E": "EMPLOYMENT STATUS", "F": "LICENSURE",
    "G": "INSTITUTIONAL/HOSPITAL", "I": "PROFESSIONAL ORGANIZATIONS",
    "J": "PERCENT EFFORT", "T": "APPENDIX",
}
_APPENDIX_CODE = "T"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


@dataclass(frozen=True)
class Flag:
    """One flag: a comment on ``paragraph`` (on ``span`` of its text, or all
    of it when None), or, with no paragraph, a bullet in the review notes."""
    paragraph: Paragraph | None
    span: tuple[int, int] | None
    text: str


def review_docx_path(clean_docx: Path) -> Path:
    """Where the flagged copy of ``<uid>_wcm.docx`` is written."""
    return clean_docx.with_name(clean_docx.name.removesuffix("_wcm.docx") + REVIEW_DOCX_SUFFIX)


def _paragraph_text(p: Paragraph) -> str:
    # ponytail: w:t only, so a tracked insertion (enrichment) counts and a
    # tracked deletion (w:delText) does not; Paragraph.text skips w:ins runs.
    return "".join(t.text or "" for t in p._p.iter(qn("w:t")))


def _norm(text: str) -> str:
    """squash, with curly quotes straightened: a source "pharmacist’s" is a
    rendered "pharmacist's" once PubMed or stage 5d has rewritten the citation."""
    return squash(text).translate(_QUOTES)


def _probes(text: str) -> list[str]:
    """Squashed windows of a quote: its start, middle and end, since stage 6
    reformats the source text a quote was taken from (labels, date forms)."""
    text = _BRACKET_LOCATOR_RE.sub("", _NOTE_LOCATOR_RE.sub("", text))
    s = _norm(text.removesuffix(TRUNCATION_MARK))
    if len(s) < MIN_QUOTE_CHARS:
        return []
    starts = {0, max(0, len(s) // 2 - QUOTE_PROBE_CHARS // 2), max(0, len(s) - QUOTE_PROBE_CHARS)}
    return [s[i:i + QUOTE_PROBE_CHARS] for i in sorted(starts)]


def _find(text: str, surfaces: tuple[list[tuple[Paragraph, str]], ...],
          later_copy: bool = False) -> Paragraph | None:
    """The paragraph holding ``text``: searched paragraph by paragraph, then
    table row by table row (a record printed across cells). A quote's start
    is its own (a repeated record matches twice; ``later_copy`` takes the
    second); a window from its middle or end recurs across records, so it
    only counts when unique."""
    for surface in surfaces:
        for n, probe in enumerate(_probes(text)):
            hits = [p for p, body in surface if probe in body]
            if hits and (n == 0 or len(hits) == 1):
                return hits[1] if later_copy and len(hits) > 1 else hits[0]
    return None


def _rows(doc: Document) -> list[tuple[Paragraph, str]]:
    """Each table row as one squashed text, anchored on the row's first paragraph."""
    rows = []
    for tr in doc.element.body.iter(qn("w:tr")):
        first = next(tr.iter(qn("w:p")), None)
        if first is not None:
            rows.append((Paragraph(first, doc._body), _norm("".join(t.text or "" for t in tr.iter(qn("w:t"))))))
    return rows


def _heading(code: str, paragraphs: list[tuple[Paragraph, str]]) -> Paragraph | None:
    """The template heading stage 6 files ``code``'s records under, found by
    stage 6's own rule (_find_paragraph_with_text), kept to heading-length paragraphs."""
    text = squash(WCMTemplateGenerator._get_wcm_section_header(code)
                  or _TOP_LEVEL_HEADINGS.get(code[:1], ""))
    if not text:
        return None
    return next((p for p, body in paragraphs if text in body and len(body) <= MAX_HEADING_CHARS), None)


def _year_span(lint: str, detail: str, para: Paragraph) -> tuple[int, int] | None:
    """The span of the year a year lint's detail names, when the paragraph prints it."""
    year = _YEAR_RE.search(detail) if lint in YEAR_LINTS else None
    at = _paragraph_text(para).find(year.group(0)) if year else -1
    return (at, at + len(year.group(0))) if year and at >= 0 else None


def _dedup_text(dropped: str) -> str:
    return f'{DEDUP_DROPPED_FLAG}: "{dropped.strip()}"'


def _item_flags(lint: str, inst: DoctorFindingInstance, label: str,
                surfaces: tuple[list[tuple[Paragraph, str]], ...]) -> list[Flag]:
    """Flags on the quoted text itself: every item for EACH_ITEM_LINTS, else the first found."""
    flags: list[Flag] = []
    for item in [*inst.quotes, *inst.notes]:
        text, target = label, item
        if lint == "dedup_drops":
            m = _DEDUP_RE.search(item)
            if m is None:
                continue
            text, target = _dedup_text(m["dropped"]), m["kept"]
        para = _find(target, surfaces, later_copy=lint in REPEAT_LINTS)
        if para is not None and all(f.paragraph is not para for f in flags):
            flags.append(Flag(para, _year_span(lint, inst.detail, para), text))
            if lint not in EACH_ITEM_LINTS:
                break
    return flags


def _flags(finding: dict, surfaces: tuple[list[tuple[Paragraph, str]], ...]) -> list[Flag]:
    """Where one finding is flagged: on its quoted text; else its section's
    heading; else a review note saying what and where it was."""
    lint, inst = finding["lint"], _instance(finding)
    label = REVIEW_FLAGS.get(lint, inst.detail)
    flags = _item_flags(lint, inst, label, surfaces)
    if flags:
        return flags
    heading = _heading(_CODE_BY_LABEL.get(inst.section or "", ""), surfaces[0])
    if heading is not None:
        return [Flag(heading, None, label)]
    where = f" ({inst.section})" if inst.section else ""
    if lint == "dedup_drops":  # its evidence is the doctor's diagnostic: quote only what was removed
        drops = [m["dropped"] for q in inst.quotes if (m := _DEDUP_RE.search(q))]
        return [Flag(None, None, _dedup_text(d) + where) for d in drops] or [
            Flag(None, None, label + where)]
    quote = f' "{inst.quotes[0]}"' if inst.quotes else ""
    return [Flag(None, None, f"{label}{where}{quote}")]


def _appendix_flags(diverted: list[DoctorFindingInstance], paragraphs: list[tuple[Paragraph, str]],
                    appendix: Paragraph) -> list[Flag]:
    """Stage 6's Appendix diversions, which name a count and never the lines
    (the sidecar carries no entry text, by design: appendix.py
    AppendixDiversionWarning). A review note says where they came from;
    each group of lines under the heading gets its own comment."""
    came_from: dict[str, int] = {}
    for inst in diverted:
        m = _DIVERTED_RE.search(inst.detail)
        where = inst.section if inst.section and inst.section != TAXONOMY_LABELS[_APPENDIX_CODE] else "no section"
        came_from[where] = came_from.get(where, 0) + (int(m["count"]) if m else 1)
    summary = ", ".join(f"{n} from {where}" if where != "no section" else f"{n} with no section"
                        for where, n in came_from.items())
    at = next(i for i, (p, _) in enumerate(paragraphs) if p is appendix)
    below = [p for p, body in paragraphs[at + 1:] if body]
    groups = [p for p in below if _paragraph_text(p).startswith(APPENDIX_GROUP_PREFIX)]
    lines = [Flag(p, None, APPENDIX_GROUP_FLAG) for p in groups] or [
        Flag(p, None, APPENDIX_LINE_FLAG) for p in below]
    return [Flag(None, None, f"Moved to the Appendix: {summary}."), *lines]


def _split(r: BaseOxmlElement, t: BaseOxmlElement, lo: int, hi: int) -> BaseOxmlElement:
    """Split run ``r`` (one w:t) so ``t.text[lo:hi]`` is a run of its own; return that run."""
    text = t.text or ""
    middle = r
    for piece, is_middle in reversed(((text[:lo], False), (text[lo:hi], True), (text[hi:], False))):
        if not piece:
            continue
        clone = copy.deepcopy(r)
        clone_t = next(clone.iter(qn("w:t")))
        clone_t.text = piece
        clone_t.set(_XML_SPACE, "preserve")
        r.addnext(clone)
        if is_middle:
            middle = clone
    r.getparent().remove(r)
    return middle


def _runs(para: Paragraph, span: tuple[int, int] | None) -> list[Run]:
    """The first and last run covering ``span`` of the paragraph's text (all
    of it when None), splitting runs at its ends. Runs inside a tracked
    insertion count; a tracked deletion's do not."""
    runs = [r for r in para._p.iter(qn("w:r")) if r.getparent().tag != qn("w:del")]
    if not runs:
        return [para.add_run()]
    covered, at = [], 0
    for r in runs if span else []:
        ts = list(r.iter(qn("w:t")))
        length = sum(len(t.text or "") for t in ts)
        lo, hi = max(span[0] - at, 0), min(span[1] - at, length)
        if lo < hi:
            covered.append(_split(r, ts[0], lo, hi) if len(ts) == 1 else r)
        at += length
    chosen = covered or runs
    return [Run(chosen[0], para), Run(chosen[-1], para)]


def _heading_style(doc: Document) -> BaseStyle:
    """Heading 2, in the notes font. The WCM template defines no heading
    styles (Word only offers its latent built-in), so add it when missing:
    Word maps a style named "Heading 2" to its built-in, navigation pane included."""
    try:
        style = doc.styles[NOTES_HEADING_STYLE]
    except KeyError:
        style = doc.styles.add_style(NOTES_HEADING_STYLE, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles["Normal"]
        style.next_paragraph_style = doc.styles["Normal"]
        style.font.bold = True
        style.font.size = NOTES_HEADING_SIZE
        style.paragraph_format.keep_with_next = True
        style.element.get_or_add_pPr().append(style.element.makeelement(qn("w:outlineLvl"), {qn("w:val"): "1"}))
    style.font.name = NOTES_FONT
    return style


def _add_review_notes(doc: Document, notes: list[str]) -> None:
    """The review notes closing the document: a blank line, a Heading 2, a bullet per note."""
    if not notes:
        return
    doc.add_paragraph()
    doc.add_paragraph(REVIEW_NOTES_HEADING, style=_heading_style(doc)).runs[0].font.name = NOTES_FONT
    for note in notes:
        doc.add_paragraph(f"{NOTE_BULLET}{note}").runs[0].font.name = NOTES_FONT


def write_review_docx(clean_docx: Path, doctor_payload: object) -> tuple[Path, int] | None:
    """Write the flagged copy of ``clean_docx``; return its path and how many
    flags (comments and review notes) it carries. None when there is nothing to flag."""
    if not isinstance(doctor_payload, dict):
        return None
    findings = [f for f in _usable_findings(doctor_payload)[0]
                if f["severity"] in COMMENTED_SEVERITIES]
    if not findings:
        return None
    doc = Document(str(clean_docx))
    paragraphs = [(p, _norm(_paragraph_text(p)))
                  for p in (Paragraph(el, doc._body) for el in doc.element.body.iter(qn("w:p")))]
    if not paragraphs:
        return None
    appendix = _heading(_APPENDIX_CODE, paragraphs)
    surfaces = (paragraphs, _rows(doc))
    flags: list[Flag] = []
    if appendix is not None:  # else each diversion is flagged like any other finding, below
        diverted = [f for f in findings
                    if f["lint"] == DIVERSION_LINT and _DIVERTED_RE.search(_instance(f).detail)]
        if diverted:
            findings = [f for f in findings if f not in diverted]
            flags = _appendix_flags([_instance(f) for f in diverted], paragraphs, appendix)
            flags = flags[:MAX_FLAGS_PER_LINT]
    per_lint: dict[str, int] = {}
    for f in findings:
        for flag in _flags(f, surfaces):
            per_lint[f["lint"]] = per_lint.get(f["lint"], 0) + 1
            if per_lint[f["lint"]] <= MAX_FLAGS_PER_LINT:
                flags.append(flag)
    for flag in flags:
        if flag.paragraph is not None:
            doc.add_comment(_runs(flag.paragraph, flag.span), text=flag.text,
                            author=COMMENT_AUTHOR, initials=COMMENT_INITIALS)
    _add_review_notes(doc, list(dict.fromkeys(f.text for f in flags if f.paragraph is None)))
    out = review_docx_path(clean_docx)
    doc.save(str(out))
    return out, len(flags)
