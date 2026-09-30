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
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from docx import Document
from docx.shared import Pt
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.page import Page
from pdfplumber.utils import cluster_objects
from pdfplumber.utils.exceptions import PdfminerException

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
#: Horizontal gap between two words above which they MAY be joined with "\t".
TAB_GAP_EM = 1.5
#: On a FULL-WIDTH line (the only kind a justified paragraph produces) a
#: gap must also be at least this many times the line's median word gap to
#: be a tab: a justified line has uniformly wide gaps (no tabs), a
#: label/value line has one outlier gap (a tab). Every other line tabs any
#: gap over TAB_GAP_EM, so 4+ column rows keep their tabs.
TAB_OUTLIER_RATIO = 2.0
#: A line with at most this many word gaps skips the median test (with one
#: or two gaps the median is the gap itself, so nothing could be an outlier).
TAB_FEW_GAPS_MAX = 2
#: A line that starts with a list marker begins a new entry, never a
#: wrapped continuation: "12." / "3)" (1-3 digits, then whitespace), "[12]",
#: a bullet glyph (U+F0B7 is Word's Symbol-font bullet), or a
#: dash/asterisk/hyphen followed by whitespace. Exceptions below.
LIST_MARKER_RE = re.compile(
    r"^(?:\d{1,3}[.)]\s|\[\d{1,3}\]|[•▪◦‣●○■□\uf0b7\u2500]|[–—*-]\s)")
#: The dash/asterisk markers, captured. A lone "- present" is a wrapped
#: range, so these only veto a merge when the paragraph itself began with
#: the same marker (a real dash list is consistent).
DASH_MARKER_RE = re.compile(r"^([–—*-])\s")
#: A previous line ending like this is mid-phrase (a wrapped page range
#: "529-" / "45.", a date range, "and"): the next line continues it even if
#: it looks like a marker.
CONNECTOR_END_RE = re.compile(r"(?:[-–—,(:&]|\b(?:and|of|the|in))$", re.IGNORECASE)
#: A line ending in a hyphen or dash is broken mid-word/mid-range ("1895-" /
#: "1904."), so it wraps on even though it stops short of the right margin.
WRAP_DASH_RE = re.compile(r"[-–—]$")
#: ...except an OPEN YEAR RANGE ("Member, Committee A, 2004-"), which is a
#: complete entry: its dash only counts as a wrap (and a connector) when
#: the next line continues the range: a digit, a month name, or
#: present/current/now.
OPEN_RANGE_RE = re.compile(r"\b(?:19|20)\d{2}\s*[-–—]$")
RANGE_CONTINUATION_RE = re.compile(
    r"^(?:\d|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|(?:present|current|now)\b)", re.IGNORECASE)
#: A line of only dashes/underscores/spaces is a horizontal rule, not a
#: line that ends in a dash.
RULE_LINE_RE = re.compile(r"^[-–—_\s]+$")
#: Hanging-indent test: a paragraph whose first line starts LEFT of its
#: later lines by more than this many points is a hanging-indent entry;
#: the next line returning to within this of the first line's x0 starts a
#: new entry. A first-line indent (first line right of later lines) is a
#: plain paragraph and never splits.
OUTDENT_TOLERANCE_PT = 3.0
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

# ponytail: bold is guessed from the font NAME. Ceiling: generic subset
# names ("CIDFont+F1") carry no weight and read as regular. Upgrade path:
# read the font descriptor (pdfminer `PDFFont.descriptor` StemV / FontWeight
# / the ForceBold flag) instead of the name.
#: Bold face names (matched case-insensitively): Bold/SemiBold/DemiBold,
#: Black, Heavy, Demi, URW "-Medi" (NimbusRomNo9L-Medi), and LaTeX bold
#: extended CMBX12 / CMSSBX10 (`bx` before the size, after the subset prefix).
#: "Medium" (HelveticaNeue-Medium) is deliberately NOT bold: `-medi(?!um)`.
BOLD_FONT_RE = re.compile(r"bold|black|heavy|demi|-medi(?!um)|(?:^|\+)cm[a-z]*bx\d")

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
    words: list[dict] = field(default_factory=list)  # x-sorted, for a re-layout

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
    first_x0: float = 0.0            # x0 of the paragraph's first line
    later_x0: float | None = None    # x0 of its second line, once there is one
    marker: str = ""                 # dash/asterisk marker its first line began with


def _is_bold(fontname: str) -> bool:
    return BOLD_FONT_RE.search(fontname.lower()) is not None


def _column_gaps(words: list[dict], outlier_test: bool = False) -> list[bool]:
    """For each pair of adjacent words: is the gap between them a column gap
    (a tab), as opposed to a word space? With `outlier_test` (a full-width
    line, which may be justified) a stretched-but-uniform gap is not one."""
    gaps = [b["x0"] - a["x1"] for a, b in zip(words, words[1:])]
    median = statistics.median(gaps) if gaps else 0.0
    exempt = not outlier_test or len(gaps) <= TAB_FEW_GAPS_MAX
    return [gap > TAB_GAP_EM * a["size"] and (exempt or gap >= TAB_OUTLIER_RATIO * median)
            for gap, a in zip(gaps, words)]


def _build_line(words: list[dict], outlier_test: bool = False) -> _Line:
    """One visual line from same-line words: runs split on (bold, size)
    change, words joined by a space, or a tab across a wide column gap."""
    words = sorted(words, key=lambda w: w["x0"])
    runs: list[_Run] = []
    column_gap = _column_gaps(words, outlier_test)
    has_tab = any(column_gap)
    for index, w in enumerate(words):
        bold, size = _is_bold(w["fontname"]), round(w["size"], 1)
        sep = ""
        if index:
            sep = "\t" if column_gap[index - 1] else " "
        if runs and sep:
            runs[-1].text += sep  # separator stays with the run on its left
        if runs and (runs[-1].bold, runs[-1].size) == (bold, size):
            runs[-1].text += w["text"]
        else:
            runs.append(_Run(w["text"], bold, size))
    return _Line(
        top=min(w["top"] for w in words),
        bottom=max(w["bottom"] for w in words),
        x0=words[0]["x0"],
        x1=max(w["x1"] for w in words),
        size=max(r.size for r in runs),
        runs=runs,
        has_tab=has_tab,
        words=words,
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


def _dash_wraps(prev: _Line, line: _Line) -> bool:
    """`prev` ends in a dash that means "continued on the next line": not a
    rule line, and not an open year range unless `line` continues it."""
    text = prev.text
    if WRAP_DASH_RE.search(text) is None or RULE_LINE_RE.match(text):
        return False
    return not OPEN_RANGE_RE.search(text) or RANGE_CONTINUATION_RE.match(line.text) is not None


def _ends_mid_phrase(prev: _Line, line: _Line) -> bool:
    """`prev` stops mid-phrase, so `line` continues it: a connector ending,
    where a dash counts only under `_dash_wraps`."""
    if WRAP_DASH_RE.search(prev.text):
        return _dash_wraps(prev, line)
    return CONNECTOR_END_RE.search(prev.text) is not None


def _marker_vetoes(prev: _Line, line: _Line, para: _Para) -> bool:
    """`line` starts a new list entry: it opens with a marker, `prev` does
    not end mid-phrase, and a dash/asterisk marker matches the marker the
    paragraph itself began with."""
    if not LIST_MARKER_RE.match(line.text) or _ends_mid_phrase(prev, line):
        return False
    dash = DASH_MARKER_RE.match(line.text)
    return dash is None or dash.group(1) == para.marker


def _outdent_splits(prev: _Line, line: _Line, para: _Para) -> bool:
    """`line` returns to the first-line x0 of a hanging-indent paragraph and
    `prev` does not end mid-phrase."""
    return (not _ends_mid_phrase(prev, line)
            and para.later_x0 is not None
            and para.later_x0 - para.first_x0 > OUTDENT_TOLERANCE_PT
            and line.x0 <= para.first_x0 + OUTDENT_TOLERANCE_PT)


def _continues(prev: _Line, line: _Line, left: float, right: float,
               para: _Para) -> bool:
    """True when `line` looks like a wrapped continuation of `prev` (the
    last line of `para`): same weight and size, no tabs, not a new list
    entry or hanging-indent entry, and `prev` ran to the right margin.
    ponytail: a hanging indent needs two lines to be seen, so a one-line
    entry followed by another entry merges unless a marker separates them."""
    if _marker_vetoes(prev, line, para) or _outdent_splits(prev, line, para):
        return False
    if prev.has_tab or line.has_tab or prev.all_bold != line.all_bold:
        return False
    if abs(prev.size - line.size) > MERGE_SIZE_TOLERANCE_PT:
        return False
    return _is_full_line(prev, left, right) or _dash_wraps(prev, line)


def _margins(pages: list[list[_Line]]) -> tuple[float, float]:
    """Left edge and right edge (text width) over every kept line."""
    lines = [ln for page in pages for ln in page]
    return min(ln.x0 for ln in lines), max(ln.x1 for ln in lines)


def _untab_justified(pages: list[list[_Line]]) -> list[list[_Line]]:
    """Re-lay-out full-width tabbed lines with the outlier test: only a
    line that reaches the right margin can be a justified, stretched one."""
    if not any(pages):
        return pages
    left, right = _margins(pages)
    return [[_build_line(ln.words, outlier_test=True)
             if ln.has_tab and _is_full_line(ln, left, right) else ln
             for ln in lines] for lines in pages]


def _assemble(pages: list[list[_Line]]) -> list[_Para]:
    """Lines -> paragraph stream, with an empty paragraph per large gap."""
    if not any(pages):
        return []
    left, right = _margins(pages)
    paras: list[_Para] = []
    prev: _Line | None = None
    for lines in pages:
        for index, line in enumerate(lines):
            same_page = index > 0
            gap = line.top - prev.bottom if (prev and same_page) else 0.0
            merge = prev is not None and gap <= PARAGRAPH_GAP_EM * prev.size \
                and _continues(prev, line, left, right, paras[-1])
            if merge:
                paras[-1].runs[-1].text += " "
                paras[-1].runs.extend(line.runs)
                if paras[-1].later_x0 is None:
                    paras[-1].later_x0 = line.x0
            else:
                if prev is not None and gap > BLANK_GAP_EM * prev.size:
                    paras.append(_Para())
                dash = DASH_MARKER_RE.match(line.text)
                paras.append(_Para(runs=list(line.runs), first_x0=line.x0,
                                   marker=dash.group(1) if dash else ""))
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
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                pages.append(_page_lines(page))
                heights.append(float(page.height))
                if _is_image_only(page):
                    image_only.append(number)
    except PdfminerException as exc:
        # pdfplumber wraps everything in a message-less PdfminerException;
        # name the one cause a caller can act on, re-raise the rest as is.
        if exc.args and isinstance(exc.args[0], PDFPasswordIncorrect):
            raise ValueError("encrypted/password-protected PDF") from exc
        raise
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
    paras = _assemble(_untab_justified(kept))
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
