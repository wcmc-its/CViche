"""PDF -> docx converter for CV ingest (#806, PR 1: converter only, no wiring).

Every pipeline reader (stages 1a/1b/2, run_doctor) opens the source through
python-docx and indexes off the paragraph stream, so a PDF has to become a
docx that looks like a native one to those readers: one paragraph per logical
line group, an EMPTY paragraph where the page has a vertical gap (stage 1a
keeps blanks as "" and later stages index positions off them), bold carried
onto runs (`run_doctor.iter_header_candidates` reads `all(r.bold ...)`), and
`\\t` where the page has a column gap.

Uses only pdfplumber + python-docx. Unreadable or encrypted PDFs raise; this
module never swallows an error.

Dependencies: third-party only (pdfplumber, python-docx). Imports nothing
from the rest of unified_pipeline.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document
from docx.shared import Pt
from pdfplumber.page import Page
from pdfplumber.utils import cluster_objects

# --- Line grouping -------------------------------------------------------

#: Words whose `bottom` differs by <= this many points share a visual line.
#: `bottom` (not `top`) because words of different sizes on one baseline
#: share a bottom but not a top. Also absorbs a superscript raised about a
#: third of an em (measured: 3pt raise on 10pt text moves `bottom` 3.7pt).
LINE_BOTTOM_TOLERANCE_PT = 4.0

# --- Spacing thresholds, in ems of the previous line's font size ---------
# ponytail: thresholds guessed from Word/LaTeX exports of the sample CVs
# (single spacing = ~0.2em between line boxes; a Word blank paragraph =
# ~1.3em). Ceiling: a CV with double-spaced body text reads as one blank
# paragraph per line. Upgrade path: derive the norm per document from the
# modal line pitch instead of a constant.

#: Gap above which a new paragraph starts (below it, lines may merge).
PARAGRAPH_GAP_EM = 0.45
#: Gap above which an empty paragraph is emitted before the new one.
BLANK_GAP_EM = 1.0
#: Horizontal gap between two words above which they are joined with "\t".
TAB_GAP_EM = 1.5
#: A line ends "full" (so the next one is a wrapped continuation) when its
#: right edge is within this fraction of the text width of the page's
#: rightmost line. Short lines are entries/list items and must not merge.
FULL_LINE_SLACK_FRAC = 0.08
#: Two lines with font sizes further apart than this never merge.
MERGE_SIZE_TOLERANCE_PT = 0.6

# --- Page furniture ------------------------------------------------------

#: A line is running furniture if it repeats at the same position on at
#: least this fraction of the pages (and on at least FURNITURE_MIN_PAGES).
FURNITURE_PAGE_FRACTION = 0.5
FURNITURE_MIN_PAGES = 2
#: Only lines within this fraction of the page height from the top or
#: bottom edge can be furniture, so a repeated body line is never dropped.
FURNITURE_BAND_FRAC = 0.12
#: Vertical position is bucketed to this many points when comparing pages.
FURNITURE_POSITION_BUCKET_PT = 3.0

# --- Reporting -----------------------------------------------------------

#: A page with fewer characters than this AND at least one image is
#: reported as image-only (scanned page; #536).
IMAGE_ONLY_MAX_CHARS = 20

#: Font-name substrings (case-insensitive) that mark a bold face.
BOLD_FONT_MARKERS = ("bold", "black", "heavy")

#: XML 1.0 forbids these; python-docx raises ValueError on them.
_XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DIGITS_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class ConversionReport:
    pages: int
    paragraphs: int
    blank_paragraphs: int
    image_only_pages: list[int]
    chars: int


@dataclass
class _Run:
    text: str
    bold: bool
    size: float


@dataclass
class _Line:
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    runs: list[_Run]
    has_tab: bool

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def all_bold(self) -> bool:
        return all(r.bold for r in self.runs if r.text.strip())


@dataclass
class _Para:
    """One output paragraph; `runs` is empty for a blank paragraph."""
    runs: list[_Run] = field(default_factory=list)


def _is_bold(fontname: str) -> bool:
    name = fontname.lower()
    return any(marker in name for marker in BOLD_FONT_MARKERS)


def _build_line(words: list[dict]) -> _Line:
    """One visual line from same-line words: runs split on (bold, size)
    change, words joined by a space, or a tab across a wide column gap."""
    words = sorted(words, key=lambda w: w["x0"])
    runs: list[_Run] = []
    has_tab = False
    prev = None
    for w in words:
        bold, size = _is_bold(w["fontname"]), round(w["size"], 1)
        sep = ""
        if prev is not None:
            wide = w["x0"] - prev["x1"] > TAB_GAP_EM * prev["size"]
            has_tab = has_tab or wide
            sep = "\t" if wide else " "
        if runs and sep:
            runs[-1].text += sep  # separator stays with the run on its left
        if runs and (runs[-1].bold, runs[-1].size) == (bold, size):
            runs[-1].text += w["text"]
        else:
            runs.append(_Run(w["text"], bold, size))
        prev = w
    return _Line(
        top=min(w["top"] for w in words),
        bottom=max(w["bottom"] for w in words),
        x0=words[0]["x0"],
        x1=max(w["x1"] for w in words),
        size=max(r.size for r in runs),
        runs=runs,
        has_tab=has_tab,
    )


def _page_lines(page: Page) -> list[_Line]:
    words = page.extract_words(extra_attrs=["fontname", "size"])
    clusters = cluster_objects(words, lambda w: w["bottom"], LINE_BOTTOM_TOLERANCE_PT)
    return [_build_line(c) for c in clusters]


def _furniture_key(line: _Line) -> tuple[int, str]:
    # Digits are masked so "Page 3 of 9" repeats as one line across pages.
    bucket = round(line.top / FURNITURE_POSITION_BUCKET_PT)
    return bucket, _DIGITS_RE.sub("#", line.text.strip())


def _in_edge_band(line: _Line, page_height: float) -> bool:
    band = FURNITURE_BAND_FRAC * page_height
    return line.top < band or line.bottom > page_height - band


def _furniture_keys(pages: list[list[_Line]], heights: list[float]) -> set:
    """Keys of lines repeated at the same edge position on enough pages."""
    counts: Counter = Counter()
    for lines, height in zip(pages, heights):
        counts.update({_furniture_key(ln) for ln in lines if _in_edge_band(ln, height)})
    needed = max(FURNITURE_MIN_PAGES, math.ceil(FURNITURE_PAGE_FRACTION * len(pages)))
    return {key for key, n in counts.items() if n >= needed}


def _is_full_line(line: _Line, left: float, right: float) -> bool:
    return line.x1 >= right - FULL_LINE_SLACK_FRAC * (right - left)


def _continues(prev: _Line, line: _Line, left: float, right: float) -> bool:
    """True when `line` looks like a wrapped continuation of `prev`: same
    weight and size, no tabs, and `prev` ran to the right margin."""
    if prev.has_tab or line.has_tab or prev.all_bold != line.all_bold:
        return False
    if abs(prev.size - line.size) > MERGE_SIZE_TOLERANCE_PT:
        return False
    return _is_full_line(prev, left, right)


def _assemble(pages: list[list[_Line]]) -> list[_Para]:
    """Lines -> paragraph stream, with an empty paragraph per large gap."""
    xs = [ln.x0 for lines in pages for ln in lines]
    if not xs:
        return []
    left = min(xs)
    right = max(ln.x1 for lines in pages for ln in lines)
    paras: list[_Para] = []
    prev: _Line | None = None
    for lines in pages:
        for index, line in enumerate(lines):
            same_page = index > 0
            gap = line.top - prev.bottom if (prev and same_page) else 0.0
            merge = prev is not None and gap <= PARAGRAPH_GAP_EM * prev.size \
                and _continues(prev, line, left, right)
            if merge:
                paras[-1].runs[-1].text += " "
                paras[-1].runs.extend(line.runs)
            else:
                if prev is not None and gap > BLANK_GAP_EM * prev.size:
                    paras.append(_Para())
                paras.append(_Para(runs=list(line.runs)))
            prev = line
    return paras


def _write_docx(paras: list[_Para], docx_path: Path) -> None:
    doc = Document()
    for para in paras:
        p = doc.add_paragraph()
        for run in para.runs:
            r = p.add_run(_XML_ILLEGAL_RE.sub("", run.text))
            r.bold = run.bold
            r.font.size = Pt(run.size)
    doc.save(str(docx_path))


def _is_image_only(page: Page) -> bool:
    return len(page.chars) < IMAGE_ONLY_MAX_CHARS and len(page.images) > 0


def convert_pdf_to_docx(pdf_path: str | Path, docx_path: str | Path) -> ConversionReport:
    """Convert `pdf_path` to a docx at `docx_path`; raises on an unreadable
    or encrypted PDF."""
    pages: list[list[_Line]] = []
    heights: list[float] = []
    image_only: list[int] = []
    # ponytail: reading order is pdfplumber's top-to-bottom per page; no
    # multi-column detection, so a two-column page interleaves its columns
    # line by line. Upgrade path: cluster words into column bands by x0 gaps
    # before line grouping.
    with pdfplumber.open(str(pdf_path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            pages.append(_page_lines(page))
            heights.append(float(page.height))
            if _is_image_only(page):
                image_only.append(number)
    furniture = _furniture_keys(pages, heights)
    # The key carries the position, and only edge-band lines enter
    # `furniture`, so a body line can never match.
    kept = [[ln for ln in lines if _furniture_key(ln) not in furniture]
            for lines in pages]
    # ponytail: no docx tables in v1. pdfplumber invents tables from
    # tab-aligned columns (10 on one CV), which sends a PDF-sourced CV down
    # a different reader path than its docx twin. Ceiling: a real ruled
    # table becomes tab-separated paragraphs. Upgrade path: emit a table only
    # for pdfplumber tables with ruling lines (strategy "lines") and >= 2
    # columns, and check element order with extract_unified_elements.
    paras = _assemble(kept)
    _write_docx(paras, Path(docx_path))
    blanks = sum(1 for p in paras if not p.runs)
    chars = sum(len(r.text) for p in paras for r in p.runs)
    return ConversionReport(
        pages=len(pages),
        paragraphs=len(paras),
        blank_paragraphs=blanks,
        image_only_pages=image_only,
        chars=chars,
    )
