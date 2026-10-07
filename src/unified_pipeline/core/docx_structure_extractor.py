"""
Word Document Structure Extractor

Extracts rich layout metadata from .docx files without converting to images.
Leverages Word's native structure (styles, lists, numbering, tables) to create
a lightweight "layout JSON" that can be fed to a text model for segmentation.

This is significantly cheaper and faster than vision-based approaches.
"""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.shared import Pt, RGBColor
from docx.table import Table, _Cell, _Row
from docx.text.paragraph import Paragraph

from unified_pipeline.stage6.normalization.pii import (
    PRE_LLM_PLACEHOLDER,
    pre_llm_bare_label_category,
    redact_pre_llm_value_of_category,
    redact_pre_llm_values,
)
from unified_pipeline.stage6.render_check import _is_column_header_row

logger = logging.getLogger(__name__)

# Word stores a text box as mc:AlternateContent: a DrawingML copy under
# mc:Choice and a VML copy of the same text under mc:Fallback (#1236).
_MC_NS = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
_MC_CHOICE = f'{{{_MC_NS}}}Choice'
_MC_FALLBACK = f'{{{_MC_NS}}}Fallback'


def rgb_to_hex(rgb: RGBColor | None) -> str:
    """Convert RGBColor to hex string."""
    if rgb is None:
        return "#000000"
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def pt_to_inches(pt: Pt | None) -> float:
    """Convert points to inches."""
    if pt is None:
        return 0.0
    return pt.inches if hasattr(pt, 'inches') else 0.0


def _iter_text_nodes(parent: BaseOxmlElement, tags: tuple[str, ...]) -> Iterator[BaseOxmlElement]:
    """The descendants of `parent` whose tag is in `tags`, in document order,
    reading ONE branch of each mc:AlternateContent (#1236).

    A plain `parent.iter(...)` descends into both the mc:Choice and the
    mc:Fallback copy of a text box and returns its text twice. This reads the
    Choice and skips a Fallback that has a Choice sibling; a Fallback with no
    Choice sibling is the only copy and is read. The skip applies only below
    `parent`, so asking for a paragraph that itself sits inside a Fallback
    still reads that paragraph.
    """
    for child in parent:
        if child.tag == _MC_FALLBACK and parent.find(_MC_CHOICE) is not None:
            continue
        if child.tag in tags:
            yield child
        yield from _iter_text_nodes(child, tags)


def get_paragraph_text(para: Paragraph, tab_char: str = ' ') -> str:
    """Extract all text from a paragraph, including nested structures.

    python-docx's Paragraph.text only concatenates text from direct w:r/w:t children.
    This function also captures text inside:
    - w:smartTag (legacy Word feature for auto-recognizing addresses, names, dates)
    - w:hyperlink
    - w:sdt (structured document tags / content controls)
    - w:ins (tracked-change insertions) -- see #557
    - mc:AlternateContent (text boxes), read once: the mc:Choice copy, never
      the mc:Fallback copy of the same text -- see #1236

    Without this, paragraphs using smartTags appear empty even though they contain text.
    """
    from docx.oxml.ns import qn

    # Walk w:t (text), w:br/w:cr (line breaks) and w:tab in document order so the
    # returned text keeps the paragraph's internal line structure. Bare w:t iteration
    # mashed multi-line paragraphs into run-on text ("CURRICULUM VITAEZachary..."),
    # which hid sub-headers from the chunk LLM once stage 1a converged onto this reader.
    # w:tab -> tab_char (space by default) on purpose: a literal tab would trip the
    # mega-entry record heuristic downstream. The walk also descends into
    # smartTag/hyperlink/sdt/ins.
    # w:noBreakHyphen is a hyphen stored as its own run element, not as a '-'
    # inside w:t; skipping it fused the words around it ('PEER-REVIEWED' read
    # 'PEERREVIEWED', a hyphenated surname lost its hyphen). It reads as '-',
    # as python-docx's Run.text reads it. w:softHyphen is only a permitted
    # break point, so it is not walked and reads as ''.
    WT, WBR, WCR, WTAB = qn('w:t'), qn('w:br'), qn('w:cr'), qn('w:tab')
    WNBH = qn('w:noBreakHyphen')
    parts = []
    for node in _iter_text_nodes(para._p, (WT, WBR, WCR, WTAB, WNBH)):
        if node.tag == WT:
            if node.text:
                parts.append(node.text)
        elif node.tag == WTAB:
            parts.append(tab_char)
        elif node.tag == WNBH:
            parts.append('-')
        else:  # w:br / w:cr -> line break
            parts.append('\n')
    return ''.join(parts)


def get_cell_text(cell, tab_char: str = ' ') -> str:
    """Extract all text from a table cell, including nested structures (#557).

    cell.text (python-docx) only concatenates w:r elements that are DIRECT
    CHILDREN of w:p, silently dropping runs nested inside w:ins/w:smartTag/w:sdt --
    a frequent mid-word loss when Word splits a tracked-change edit across runs
    ("Down Syndrome" -> "Down yndrome"). Reuses get_paragraph_text's wrapper-descent
    logic per paragraph rather than re-deriving it here.
    """
    return '\n'.join(get_paragraph_text(p, tab_char=tab_char) for p in cell.paragraphs)


def _unique_row_cells(table: Table) -> list[list[_Cell]]:
    """Each row's cells, with a horizontally merged cell listed once."""
    rows = []
    for row in table.rows:
        seen: set[Any] = set()
        cells = []
        for cell in row.cells:
            if cell._tc not in seen:
                seen.add(cell._tc)
                cells.append(cell)
        rows.append(cells)
    return rows


def _iter_cell_paragraphs(cell: _Cell) -> Iterator[Paragraph]:
    """Yield a cell's paragraphs in document order, descending into any table
    nested in the cell (#1231). `cell.paragraphs` skips nested tables."""
    for item in cell.iter_inner_content():
        if isinstance(item, Table):
            seen_cells: set[Any] = set()   # a merged cell repeats across spanned rows/cols
            for row in item.rows:
                for nested_cell in row.cells:
                    if nested_cell._tc in seen_cells:
                        continue
                    seen_cells.add(nested_cell._tc)
                    yield from _iter_cell_paragraphs(nested_cell)
        else:
            yield item


def _cell_has_nested_table(cell: _Cell) -> bool:
    return bool(cell._tc.findall(qn('w:tbl')))


def _nested_table_lines(table: Table, tab_char: str) -> list[str]:
    """One line per non-empty row of a nested table: its cells' text, space-joined."""
    lines = []
    for cells in _unique_row_cells(table):
        row_text = ' '.join(
            t for t in (_cell_text_in_order(c, tab_char).strip() for c in cells) if t
        )
        if row_text:
            lines.append(row_text)
    return lines


def _cell_text_in_order(cell: _Cell, tab_char: str = ' ') -> str:
    """Cell text in document order, with each nested table's rows as lines (#1231).

    Same as `get_cell_text` for a cell with no nested table."""
    lines = []
    for item in cell.iter_inner_content():
        if isinstance(item, Table):
            lines.extend(_nested_table_lines(item, tab_char))
        else:
            lines.append(get_paragraph_text(item, tab_char=tab_char))
    return '\n'.join(lines)


def get_cell_text_with_nested(cell: _Cell) -> str:
    """`get_cell_text`, plus the rows of any table nested in a cell that has text
    of its own, in document order (#1231). A cell holding ONLY a nested table
    returns "" as before, so callers' itertext() fallback is unchanged."""
    text = get_cell_text(cell)
    if text.strip() and _cell_has_nested_table(cell):
        return _cell_text_in_order(cell)
    return text


def extract_paragraph_metadata(para: Paragraph, idx: int | None) -> dict[str, Any]:
    """
    Extract comprehensive metadata from a paragraph.

    Returns layout JSON with:
    - Text content
    - Style name (Heading 1/2/3, etc.)
    - List level and numbering format
    - Indentation
    - Font properties (bold, italic, size, color)
    - Alignment
    """

    # Basic text — use helper to capture smartTag content
    text = get_paragraph_text(para).strip()

    # Style
    style_name = para.style.name if para.style else "Normal"

    # List/numbering properties
    list_level = None
    num_fmt = None
    if para._p.pPr is not None:
        num_pr = para._p.pPr.numPr
        if num_pr is not None:
            if num_pr.ilvl is not None:
                list_level = num_pr.ilvl.val
            if num_pr.numId is not None:
                # Could extract actual numbering format from numbering.xml
                # For now, just note it's numbered
                num_fmt = "numbered"

    # Indentation
    indent_left = 0.0
    indent_first = 0.0
    if para.paragraph_format.left_indent:
        indent_left = pt_to_inches(para.paragraph_format.left_indent)
    if para.paragraph_format.first_line_indent:
        indent_first = pt_to_inches(para.paragraph_format.first_line_indent)

    # Alignment
    alignment = None
    if para.paragraph_format.alignment is not None:
        alignment_map = {
            WD_ALIGN_PARAGRAPH.LEFT: "left",
            WD_ALIGN_PARAGRAPH.CENTER: "center",
            WD_ALIGN_PARAGRAPH.RIGHT: "right",
            WD_ALIGN_PARAGRAPH.JUSTIFY: "justify"
        }
        alignment = alignment_map.get(para.paragraph_format.alignment, "left")

    # Font properties from first run (representative)
    bold = False
    italic = False
    underline = False
    font_size = None
    font_color = "#000000"

    if para.runs:
        first_run = para.runs[0]
        if first_run.bold is not None:
            bold = first_run.bold
        if first_run.italic is not None:
            italic = first_run.italic
        if first_run.underline is not None:
            underline = first_run.underline
        if first_run.font.size:
            font_size = first_run.font.size.pt
        if first_run.font.color and first_run.font.color.rgb:
            font_color = rgb_to_hex(first_run.font.color.rgb)

    # Outline level (from style or explicit)
    outline_level = None
    if para.style and hasattr(para.style, 'base_style'):
        # Check if it's a heading style
        if 'Heading' in style_name:
            try:
                outline_level = int(style_name.split()[-1])
            except (ValueError, IndexError):
                pass

    return {
        "idx": idx,
        "type": "paragraph",
        "text": text,
        "style": style_name,
        "outline_level": outline_level,
        "list_level": list_level,
        "num_fmt": num_fmt,
        "indent_left": round(indent_left, 3),
        "indent_first": round(indent_first, 3),
        "alignment": alignment,
        "bold": bold,
        "italic": italic,
        "underline": underline,
        "font_size": font_size,
        "font_color": font_color
    }


def _is_date_column(lines: list[str]) -> bool:
    """
    Check if a list of lines looks like a date column.

    Date columns typically contain year patterns (e.g., "1998", "1998-2007", "2020-present").
    Used to detect date columns that should be distributed across split rows even when
    their line count doesn't match.

    Args:
        lines: List of text lines from a cell

    Returns:
        True if most lines contain date-like patterns
    """
    import re
    # Year pattern: 4-digit year, optionally with range (e.g., "1998", "1998-2007", "2020-present")
    year_pattern = re.compile(r'\b(19|20)\d{2}\b')

    if not lines:
        return False

    date_lines = sum(1 for line in lines if year_pattern.search(line))
    # If most lines contain years, it's likely a date column
    return date_lines >= len(lines) * 0.5 and date_lines >= 1


def _fit_lines_to_slots(lines: list[str], slots: int) -> list[str]:
    """Return exactly `slots` strings holding every line of `lines`.

    Fewer lines than slots: pad with "" at the end. More lines than slots: the
    surplus is joined onto the last slot, never dropped (#612: a 4-line date
    column beside a cell that split into 2 segments lost its last 2 dates).
    """
    if len(lines) > slots:
        logger.info(
            "split_merged_cells_in_row: %d lines for %d sub-rows; folding %d surplus into the last",
            len(lines), slots, len(lines) - slots,
        )
        return lines[:slots - 1] + ["\n".join(lines[slots - 1:])]
    return lines + [""] * (slots - len(lines))


_GRID_KEYS = ("grid_col", "grid_span")


def _carry_grid_metadata(source_row: list[Any], new_row: list[Any]) -> None:
    """Copy each source cell's layout-column keys onto the cell built at the
    same position, so a split row still maps its cells to layout columns
    (`_cell_at_grid_col`). Metadata only; the text is untouched."""
    for src, new in zip(source_row, new_row):
        if new is src or not isinstance(src, dict) or not isinstance(new, dict):
            continue
        for key in _GRID_KEYS:
            if key in src:
                new.setdefault(key, src[key])


def split_merged_cells_in_row(row: list[dict[str, Any]], min_chars: int = 50, min_newlines: int = 2) -> list[list[dict[str, Any]]]:
    """
    Split a table row into multiple rows if any cell contains merged content.

    This handles two patterns:
    1. Double newlines (\\n\\n): Explicit entry separators like "MBA\\n\\nBS"
    2. Aligned single newlines: When 2+ cells have the same line count, indicating
       corresponding entries (e.g., Cell 0 has 3 roles, Cell 1 has 3 dates)

    The function intelligently handles multi-column tables:
    - If multiple cells have the same number of splits, splits them in parallel
    - If only one cell has splits, other cells are duplicated across split rows
    - If cells have different numbers of splits, uses the max and pads shorter ones

    Args:
        row: List of cell dictionaries with at least "text" key
        min_chars: Minimum characters for a segment to trigger splitting
        min_newlines: Minimum newlines in segment to trigger splitting

    Returns:
        List of rows - either [original_row] if no splitting needed, or multiple rows
    """
    if not row:
        return [row]

    # First pass: check for double-newline splits (\n\n)
    cell_splits = []
    max_splits = 1

    for cell in row:
        cell_text = cell.get("text", "") if isinstance(cell, dict) else str(cell)

        if '\n\n' in cell_text:
            segments = [s.strip() for s in cell_text.split('\n\n') if s.strip()]
            # Check if ANY segment is substantial (to decide whether to split)
            has_substantial = any(
                len(s) > min_chars or s.count('\n') >= min_newlines
                for s in segments
            )
            # Also consider: if we have 2+ non-trivial segments, split even if short
            # This handles cases like "MBA\n\nBS" where both are short but valid
            has_multiple_items = len(segments) >= 2 and all(len(s) >= 2 for s in segments)

            if has_substantial or has_multiple_items:
                # Keep ALL segments, not just substantial ones
                cell_splits.append(segments)
                max_splits = max(max_splits, len(segments))
            else:
                cell_splits.append(None)  # No split needed
        else:
            cell_splits.append(None)

    # Second pass: check for aligned single-newline patterns
    # If no \n\n splits were found, check if multiple cells have matching line counts
    if max_splits == 1:
        # Count lines in each cell (split by single \n)
        line_counts = []
        cell_lines = []
        for cell in row:
            cell_text = cell.get("text", "") if isinstance(cell, dict) else str(cell)
            lines = [line.strip() for line in cell_text.strip().split('\n') if line.strip()]
            line_counts.append(len(lines))
            cell_lines.append(lines)

        # Find the most common line count >= 3 (to avoid splitting single-line or 2-line content)
        from collections import Counter
        count_freq = Counter(c for c in line_counts if c >= 3)

        if count_freq:
            # Get the most common multi-line count that appears in 2+ cells
            most_common = count_freq.most_common()
            for target_count, freq in most_common:
                if freq >= 2:  # At least 2 cells have this many lines
                    # Use this count for aligned splitting
                    max_splits = target_count
                    cell_splits = []
                    for cell_idx, lines in enumerate(cell_lines):
                        if line_counts[cell_idx] == target_count:
                            cell_splits.append(lines)
                        elif _is_date_column(lines):
                            # Date column with fewer lines - try to distribute dates
                            # Pad with empty strings to match target_count
                            cell_splits.append(_fit_lines_to_slots(lines, target_count))
                        else:
                            cell_splits.append(None)  # Don't split non-matching cells
                    break

    # If no splits needed, return original row
    if max_splits == 1:
        return [row]

    # Create split rows
    split_rows = []
    for split_idx in range(max_splits):
        new_row = []
        for cell_idx, cell in enumerate(row):
            cell_text = cell.get("text", "") if isinstance(cell, dict) else str(cell)
            splits = cell_splits[cell_idx]

            if splits is not None and split_idx < len(splits):
                # Use the corresponding split segment
                new_cell = {"text": splits[split_idx]}
                # Preserve other cell metadata if present
                if isinstance(cell, dict):
                    for key in ["row", "col"]:
                        if key in cell:
                            new_cell[key] = cell[key]
                new_row.append(new_cell)
            elif splits is not None:
                # This cell has fewer splits than max - use empty for extras
                new_row.append({"text": ""})
            else:
                # No split for this cell - try to distribute dates if it's a date column
                cell_lines_local = [line.strip() for line in cell_text.strip().split('\n') if line.strip()]
                if _is_date_column(cell_lines_local) and len(cell_lines_local) > 1:
                    # It's a date column - distribute dates across split rows
                    slots = _fit_lines_to_slots(cell_lines_local, max_splits)
                    new_row.append({"text": slots[split_idx]})
                elif split_idx == 0:
                    new_row.append(cell)
                else:
                    # Don't duplicate non-split cells - often labels that shouldn't repeat
                    new_row.append({"text": ""})

        _carry_grid_metadata(row, new_row)
        split_rows.append(new_row)

    padded_cols = {i for i, sp in enumerate(cell_splits) if sp is not None and len(sp) < max_splits}
    return _fold_orphan_date_tail(split_rows, padded_cols)


_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DATE_FILLER_WORDS = r"present|current|ongoing|to|issue"
_DATE_WORD_RE = re.compile(rf"\b(?:{_MONTH_NAMES}|{_DATE_FILLER_WORDS})\b\.?", re.IGNORECASE)
_DATE_PUNCT_RE = re.compile(r"[\d\s/.,\-\u2013\u2014()]*")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _is_date_only_text(text: str) -> bool:
    """True if `text` is nothing but a date expression ("May 2019", "07/2008 - Present").

    Stricter than `_is_date_column`, which accepts any line containing a year
    ("Johns Hopkins University, 1991" is not a date).
    """
    if not _YEAR_RE.search(text):
        return False
    return _DATE_PUNCT_RE.fullmatch(_DATE_WORD_RE.sub("", text)) is not None


def _is_orphan_date_row(row: list[dict[str, Any]], padded_cols: set[int]) -> bool:
    """True if every blank cell is split-padding and every non-empty cell is date-only.

    A blank cell that was never split (a role held over two stints, #886) is
    a deliberate continuation row, not an orphan.
    """
    texts = [c.get("text", "").strip() for c in row if isinstance(c, dict)]
    blank_cols = {i for i, t in enumerate(texts) if not t}
    filled = [t for t in texts if t]
    return (
        bool(filled) and bool(blank_cols) and blank_cols <= padded_cols
        and all(_is_date_only_text(t) for t in filled)
    )


def _fold_orphan_date_tail(
    split_rows: list[list[dict[str, Any]]], padded_cols: set[int]
) -> list[list[dict[str, Any]]]:
    """Merge a trailing run of date-only rows into the last row that has content.

    When a date column has more \\n\\n segments than the name column, the split
    pads the name cell with "" and the overflow becomes rows that are nothing
    but a date -- the name is not recoverable by position (#259, EH4XXA: 28
    bare-date rows). Emitting them standalone yields date-only entries with no
    subject; folding them into the preceding row keeps every date and keeps it
    beside the name cell it overflowed from. Only a contiguous tail is folded,
    and only where the blank cell is split padding (`padded_cols`).
    """
    tail_start = len(split_rows)
    while tail_start > 1 and _is_orphan_date_row(split_rows[tail_start - 1], padded_cols):
        tail_start -= 1
    if tail_start == len(split_rows) or _is_orphan_date_row(split_rows[tail_start - 1], padded_cols):
        return split_rows

    anchor = [dict(cell) for cell in split_rows[tail_start - 1]]
    for orphan in split_rows[tail_start:]:
        for col, cell in enumerate(orphan):
            text = cell.get("text", "").strip()
            if text:
                anchor[col]["text"] = f"{anchor[col].get('text', '')}\n{text}".strip("\n")
    return split_rows[:tail_start - 1] + [anchor]


# A table's row 0 is flagged a column header (#424) only when the table has a
# data row beneath it and row 0 carries at least this many distinct non-empty
# cells. A one-row "Committee | Chair" table, or a lone "Name" cell, is far
# likelier a record than a header.
COLUMN_HEADER_MIN_ROWS = 2
COLUMN_HEADER_MIN_DISTINCT_CELLS = 2
# w:val values that switch a `w:tblHeader` (Word's "Repeat as header row")
# element off.
_TBL_HEADER_OFF_VALUES = frozenset({"0", "false", "off"})


def _row_marked_repeat_header(row: _Row) -> bool:
    """True when Word's own "Repeat as header row" property is set on `row`."""
    tr_pr = row._tr.trPr
    marker = tr_pr.find(qn("w:tblHeader")) if tr_pr is not None else None
    return marker is not None and marker.get(qn("w:val"), "1").lower() not in _TBL_HEADER_OFF_VALUES


def is_column_header_row(cell_texts: list[str], marked_repeat_header: bool) -> bool:
    """Whether a table's row 0 (its cell texts) is a column-label row (#424).

    Every test is conservative, because a wrongly-flagged row is a record
    dropped from stage 2: at least two distinct non-empty cells; no digit
    anywhere (a dated row is a record, a label row never carries a year); and
    either Word marks the row as a repeating header or a majority of its words
    are column-label vocabulary (`_is_column_header_row`, the same test #736's
    appendix filter applies to a header row that already leaked). A cell
    ending in ":" makes it a label|value form row, so the vocabulary path
    refuses it ("Name: | Example" is 50% vocabulary and still a value row).
    """
    filled = {text.strip() for text in cell_texts if text.strip()}
    if len(filled) < COLUMN_HEADER_MIN_DISTINCT_CELLS:
        return False
    if any(ch.isdigit() for text in filled for ch in text):
        return False
    if marked_repeat_header:
        return True
    if any(text.endswith(":") for text in filled):
        return False
    return _is_column_header_row(" | ".join(sorted(filled)))


def _distinct_row_cells(row: _Row) -> list[tuple[int, int, _Cell]]:
    """The row's cells as ``(grid_col, grid_span, cell)``, one per distinct
    ``<w:tc>`` (#1229).

    python-docx's ``row.cells`` repeats a gridSpan-merged cell once per layout
    column it spans, so a merged cell's text would reach stage 2 several
    times. Dedupe within the row only, by element identity: two distinct
    cells with equal text are both kept. ``grid_col`` is the cell's first
    layout column and ``grid_span`` how many it covers, so a consumer that
    pairs cells across rows by column (``_scrub_pre_llm_pii_column``) still
    lines up when rows merge cells differently. A vertically merged cell is
    a distinct ``<w:tc>`` in each row (python-docx hands back the top cell
    for a continuation row) and keeps its existing row-by-row behaviour.
    """
    cells: list[tuple[int, int, _Cell]] = []
    for grid_col, cell in enumerate(row.cells):
        for i, (start, span, kept) in enumerate(cells):
            if cell._tc is kept._tc:
                cells[i] = (start, span + 1, kept)
                break
        else:
            cells.append((grid_col, 1, cell))
    return cells


def _is_layout_box(table: Table) -> bool:
    """Is this table a single-column LAYOUT box: every row one logical cell?

    Counts distinct ``<w:tc>`` per row, not ``row.cells``: python-docx repeats
    a gridSpan-merged cell once per layout column it spans, so a 1x1 box built
    on a multi-column grid read as a data row and was not exploded (#1229).
    Same rule as ``run_doctor._is_single_column`` (#749).
    """
    return bool(table.rows) and all(len(_distinct_row_cells(row)) == 1 for row in table.rows)


def extract_table_metadata(table: Table, idx: str) -> dict[str, Any]:
    """
    Extract table structure and content.

    `idx` is the table's element id, always a `"table_N"` string: stage 2
    branches on `.startswith("table_")` and derives sort keys from it (#315).

    Returns table as array of rows with cell metadata. When row 0 is a
    column-label row (`is_column_header_row`, #424) the result also carries
    `"header_row": True`; the key is absent otherwise, so an unflagged table's
    shape is unchanged.
    """
    rows_data = []

    for row_idx, row in enumerate(table.rows):
        cells_data = []
        for col_idx, (grid_col, grid_span, cell) in enumerate(_distinct_row_cells(row)):
            # FIX: cell.text sometimes returns empty string for cells with complex formatting
            # or malformed XML (e.g., <w:rPr> inside <w:t> instead of as sibling)
            cell_text = get_cell_text_with_nested(cell).strip()

            # Fallback: Extract text directly from XML using recursive text extraction.
            # No try/except: lxml's itertext() on a parsed element does not raise,
            # and a bare except here hid real bugs as empty cells (#611).
            if not cell_text and cell._element is not None:
                cell_text = ''.join(cell._element.itertext()).strip()

            # Get cell formatting if available
            cell_data = {
                "row": row_idx,
                "col": col_idx,
                "grid_col": grid_col,
                "grid_span": grid_span,
                "text": cell_text
            }

            cells_data.append(cell_data)

        rows_data.append(cells_data)

    metadata = {
        "idx": idx,
        "type": "table",
        "rows": len(table.rows),
        "cols": len(table.columns),
        "data": rows_data
    }
    if (
        len(table.rows) >= COLUMN_HEADER_MIN_ROWS
        and is_column_header_row(
            [cell["text"] for cell in rows_data[0]],
            _row_marked_repeat_header(table.rows[0]),
        )
    ):
        metadata["header_row"] = True
    return metadata


# Known CV section header keywords for table header detection
CV_SECTION_KEYWORDS = {
    # Education & Training
    "education", "degrees", "training", "academic background", "qualifications",
    # Positions & Employment
    "positions", "appointments", "employment", "experience", "professional experience",
    "academic positions", "professional appointments", "work experience",
    # Publications & Scholarship
    "publications", "articles", "papers", "manuscripts", "peer-reviewed",
    "book chapters", "chapters", "books", "abstracts", "proceedings",
    "bibliography", "scholarly works", "research publications",
    # Grants & Funding
    "grants", "funding", "research support", "grant support", "sponsored research",
    "extramural funding", "intramural funding", "contracts",
    # Teaching
    "teaching", "courses", "instruction", "lectures", "curriculum",
    "courses taught", "teaching experience",
    # Mentoring & Supervision
    "mentoring", "mentorship", "supervision", "advisees", "students supervised",
    "graduate students", "postdoctoral", "trainees", "theses supervised",
    # Presentations & Talks
    "presentations", "talks", "invited talks", "lectures", "seminars",
    "conference presentations", "symposia", "keynote",
    # Service & Leadership
    "service", "committees", "leadership", "professional service",
    "editorial", "review", "reviewer", "ad hoc reviewer",
    # Awards & Honors
    "awards", "honors", "recognition", "prizes", "fellowships",
    # Affiliations
    "affiliations", "memberships", "professional affiliations",
    "professional memberships", "societies",
    # Other common sections
    "research", "research statement", "research interests",
    "patents", "intellectual property", "inventions",
    "media", "press", "outreach", "public engagement",
    "clinical", "clinical experience", "licensure", "certifications",
    "skills", "languages", "technical skills",
    "references", "personal", "contact", "curriculum vitae",
    "workshop", "participation", "travel",
}


# looks_like_section_header's additive signals and their weights (#404: each
# signal's contribution is emitted as `header_signal_scores` beside
# `header_confidence`, so a mis-scored header shows which signal carried it).
HEADER_SIGNAL_KEYWORD = "keyword"  # a CV_SECTION_KEYWORDS term appears anywhere
HEADER_SIGNAL_ALL_CAPS = "all_caps"  # ALL CAPS, over 3 characters
HEADER_SIGNAL_TITLE_CASE = "title_case"  # at most 6 words, every word capitalized
HEADER_SIGNAL_TRAILING_COLON = "trailing_colon"
HEADER_SIGNAL_SHORT = "short"  # 1-5 words
HEADER_SIGNAL_SHORT_KEYWORD = "short_keyword"  # at most 2 words and a keyword
HEADER_SIGNAL_WEIGHTS: dict[str, float] = {
    HEADER_SIGNAL_KEYWORD: 0.4,
    HEADER_SIGNAL_ALL_CAPS: 0.3,
    HEADER_SIGNAL_TITLE_CASE: 0.2,
    HEADER_SIGNAL_TRAILING_COLON: 0.1,
    HEADER_SIGNAL_SHORT: 0.1,
    HEADER_SIGNAL_SHORT_KEYWORD: 0.2,
}
# Capped confidence at or above which looks_like_section_header calls a line a header.
SECTION_HEADER_MIN_CONFIDENCE = 0.4


class HeaderScore(NamedTuple):
    """score_section_header's result: the capped total plus the weight of
    each signal that fired, keyed by its HEADER_SIGNAL_* name."""

    is_header: bool
    confidence: float
    signal_scores: dict[str, float]


def _rejected_header_score() -> HeaderScore:
    """A fresh not-a-header score, so no caller shares a mutable signal dict."""
    return HeaderScore(is_header=False, confidence=0.0, signal_scores={})


def score_section_header(text: str) -> HeaderScore:
    """Score whether text looks like a CV section header, keeping each
    additive signal's contribution (#404) beside the capped total.

    A line rejected outright (too long, a date range, a citation) scores
    0.0 with no signals.
    """
    if not text:
        return _rejected_header_score()

    # For multi-line text, check just the first line
    # (table cells often have header on first line, content below)
    first_line = text.strip().split('\n')[0].strip()
    text_clean = first_line
    text_lower = text_clean.lower()

    # Too long to be a header (headers are typically short)
    if len(text_clean) > 100:
        return _rejected_header_score()

    # Contains date patterns - likely content, not header
    import re
    date_patterns = [
        r'\b\d{4}\s*[-–]\s*\d{4}\b',  # 2020-2024
        r'\b\d{4}\s*[-–]\s*present\b',  # 2020-present
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',  # MM/DD/YYYY
        r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b',  # January 2024
    ]
    for pattern in date_patterns:
        if re.search(pattern, text_lower):
            return _rejected_header_score()

    # Contains citation patterns - likely publication, not header
    citation_patterns = [
        r'\(\d{4}\)',  # (2024)
        r'\bet\s+al\.',  # et al.
        r'\bpp?\.\s*\d+',  # p. 123 or pp. 123-456
        r'\bvol\.\s*\d+',  # vol. 12
        r'\bdoi:',  # DOI
        r'\bpmid:',  # PMID
    ]
    for pattern in citation_patterns:
        if re.search(pattern, text_lower):
            return _rejected_header_score()

    signal_scores = _header_signal_scores(text_clean, text_lower)
    # Accumulated left to right with `+=`, NOT `sum()`: Python 3.12+'s sum()
    # compensates float error, which turns the 0.9000000000000001 this scorer
    # has always produced into 0.9 and moves a header's confidence. Capped at
    # 1.0, so the contributions can sum past the reported confidence.
    confidence = 0.0
    for weight in signal_scores.values():
        confidence += weight
    confidence = min(confidence, 1.0)
    return HeaderScore(
        is_header=confidence >= SECTION_HEADER_MIN_CONFIDENCE,
        confidence=confidence,
        signal_scores=signal_scores,
    )


def _header_signal_scores(text_clean: str, text_lower: str) -> dict[str, float]:
    """Return `{signal name: weight}` for each additive header signal that
    fires on a first line already past the length/date/citation rejections,
    in the order `score_section_header` sums them."""
    words = text_clean.split()
    has_keyword = any(kw in text_lower for kw in CV_SECTION_KEYWORDS)
    fired = (
        (HEADER_SIGNAL_KEYWORD, has_keyword),
        (HEADER_SIGNAL_ALL_CAPS, text_clean.isupper() and len(text_clean) > 3),
        (HEADER_SIGNAL_TITLE_CASE, len(words) <= 6 and all(w[0].isupper() for w in words if w)),
        (HEADER_SIGNAL_TRAILING_COLON, text_clean.endswith(':')),
        (HEADER_SIGNAL_SHORT, 1 <= len(words) <= 5),
        (HEADER_SIGNAL_SHORT_KEYWORD, len(words) <= 2 and has_keyword),
    )
    return {name: HEADER_SIGNAL_WEIGHTS[name] for name, hit in fired if hit}


def looks_like_section_header(text: str) -> tuple[bool, float]:
    """
    Determine if text looks like a CV section header.

    Returns:
        (is_header, confidence) tuple where confidence is 0.0-1.0;
        `score_section_header` also returns each signal's contribution.
    """
    score = score_section_header(text)
    return score.is_header, score.confidence


def row_has_nonblank_value_cells(row: list[dict[str, Any]]) -> bool:
    """Return True if any cell after row[0] carries non-blank text that
    DIFFERS (case-insensitively) from row[0]'s own text.

    Distinguishes a form-style label|value row (e.g. "Name:" | "<value>")
    from a genuine sub-header row: a single-cell row, a multi-cell row whose
    trailing cells are all blank, or a horizontally merged (gridSpan) row
    whose trailing cells just echo cell 0's own text, has nothing to lose by
    being emitted as a header (see issue #811).

    `extract_table_metadata` now emits a gridSpan-merged cell once (#1229), so
    a merged row's trailing cells no longer echo cell 0. The row[0]-text
    exclusion stays for rows whose cells are still repeated, and has the
    trade-off that a row with two textually IDENTICAL but structurally
    distinct trailing cells is treated like a merge -- not observed in the
    corpus.
    """
    if not row:
        return False
    first = row[0]
    label_norm = (first.get("text", "") if isinstance(first, dict) else str(first)).strip().casefold()
    for cell in row[1:]:
        cell_value = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        cell_value = cell_value.strip()
        if cell_value and cell_value.casefold() != label_norm:
            return True
    return False


# Minimum looks_like_section_header confidence for a colon-less, row-left label to
# be treated as a REAL section header (rather than a form-style label) when it also
# has distinct right-hand content -- the "header-left / content-right" table layout
# (#811 round 2, web064). Corpus floor: web064's genuine ALL-CAPS headers with no
# colon ("WORK ADDRESS", "BOARD CERTIFICATION", "POSTGRADATE") each score exactly
# 0.6 under looks_like_section_header. Colon-terminated form labels are excluded by
# the trailing-colon check below regardless of their own confidence -- e.g. web207's
# "BUSINESS ADDRESS:" scores 0.7, higher than several of web064's real headers, but
# must still stay content-only.
HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE = 0.6


def is_header_left_content_right_row(cell_text: str, header_confidence: float) -> bool:
    """Return True when a row's first cell is a real section header (not a
    colon-terminated form label) with high enough confidence for the
    "header-left / content-right" table layout (#811 round 2, web064:
    `WORK ADDRESS | <address>`).

    Such a row keeps its `table_header` AND has its content recovered into
    `current_content_rows`, unlike a form-style label|value row (#811 round 1,
    e.g. `Name: | <value>`), which is content-only.

    The only caller (`_classify_subheader_row_content`) gates on
    `row_has_nonblank_value_cells(row)` first -- the same distinct-non-blank-
    trailing-cell predicate keyed on row[0]'s text -- so the per-row check that
    used to live here (`row_has_distinct_nonblank_value_cells`) was a duplicate
    and was removed (#811 review r4025634341).
    """
    return (
        not cell_text.rstrip().endswith(":")
        and header_confidence >= HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE
    )


def _host_header_first(cell: _Cell, text: str) -> str:
    """Keep a host cell's own header line first when a nested table precedes it (#1231).

    `text` is in document order, so a nested table ahead of the host's own header
    paragraph would push that header off line 1 and `looks_like_section_header`
    would fail, demoting a table that was a `table_header` before the nested text
    was read. Hoist the cell's own first line only when it is itself header-like
    and the nested lines would otherwise lead; no text is dropped."""
    own = next((t for t in (p.strip() for p in get_cell_text(cell).split('\n')) if t), '')
    lines = text.split('\n')
    if not own or lines[0].strip() == own or not looks_like_section_header(own)[0]:
        return text
    lines.pop(next(i for i, ln in enumerate(lines) if ln.strip() == own))
    return '\n'.join([own, *lines])


def get_table_first_cell_text(table: Table) -> str:
    """Extract text from the first cell of first row of a table."""
    if not table.rows:
        return ""
    first_row = table.rows[0]
    if not first_row.cells:
        return ""
    first_cell = first_row.cells[0]

    cell_text = _host_header_first(first_cell, get_cell_text_with_nested(first_cell).strip())

    # Fallback extraction if needed
    if not cell_text and first_cell._element is not None:
        cell_text = ''.join(first_cell._element.itertext()).strip()

    return cell_text


def flatten_table_to_text(table_data: dict[str, Any], skip_first_row: bool = False) -> str:
    """
    Flatten table data to plain text.

    Args:
        table_data: Table metadata dict with 'data' key containing rows
        skip_first_row: If True, skip first row (used when first row is header)

    Returns:
        Flattened text with rows separated by newlines, cells by tabs
    """
    rows = table_data.get("data", [])
    start_row = 1 if skip_first_row else 0

    row_texts = []
    for row in rows[start_row:]:
        cell_texts = [cell.get("text", "") for cell in row]
        # Filter out empty cells and join
        non_empty = [t for t in cell_texts if t.strip()]
        if non_empty:
            row_texts.append("\t".join(non_empty))

    return "\n".join(row_texts)


def _classify_subheader_row_content(
    cell_text: str, row_header_conf: float, row: list[dict[str, Any]]
) -> tuple[bool, bool]:
    """Return `(has_value_cells, header_left_content_right)` for a table row
    whose first cell looks like a section header (#811 round 1/2): whether
    it carries a non-blank, non-label trailing value at all, and whether
    that value additionally qualifies for the header-left/content-right
    layout (real header, no colon, distinct content -- web064).

    Pure move out of `extract_unified_elements` (#811 round 3, finding 2).
    """
    has_value_cells = row_has_nonblank_value_cells(row)
    header_left_content_right = has_value_cells and is_header_left_content_right_row(
        cell_text, row_header_conf
    )
    return has_value_cells, header_left_content_right


def _table_header_element(
    unified_idx: int, text: str, table_index: int, score: HeaderScore, style: str
) -> dict[str, Any]:
    """Build a `table_header` element for a table-sourced header line, with
    its scorer's per-signal contributions beside `header_confidence` (#404)."""
    return {
        "unified_idx": unified_idx,
        "type": "table_header",
        "text": text,
        "table_index": table_index,
        "header_confidence": score.confidence,
        "header_signal_scores": dict(score.signal_scores),
        "is_header_candidate": True,
        # Paragraph-like metadata for header detection; a table header is
        # assumed bold-like.
        "bold": True,
        "style": style,
        "alignment": None,
        "font_size": None,
    }


def _handle_table_row_zero(
    table_data: dict[str, Any],
    first_cell_text: str,
    header_score: HeaderScore,
    unified_idx: int,
    num_tables: int,
) -> tuple[list[dict[str, Any]], int, int, list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
    """Handle a table's row 0 once its first cell has already tested as
    header-like: emit it as a `table_header` element, UNLESS it is itself a
    form-style label|value row (#811 round 2, web207 "NAME: | <value>"), in
    which case it is left for the per-row walk to recover as content
    instead. Also seeds `current_content_rows` with any text found after the
    header line in the same cell (e.g. "K. EXTRAMURAL...\\nAssociation of
    Pediatric...") and, per #886, with row 0's OTHER cells when they carry a
    genuine value the header text does not otherwise account for (e.g. a
    one-row "Graduate Research Assistant" | "Aug 2019-May 2022" table).

    Pure move out of `extract_unified_elements` (#811 round 3, finding 2) --
    behaviour unchanged except for the #886 addition noted above.

    Deliberately NOT reusing the header-left/content-right escape
    (`is_header_left_content_right_row`) here: routing a non-colon row 0
    into the per-row walk below also exposes it to that walk's separate
    `\\n\\n`-embedded-header splitter, which builds single-cell synthetic
    rows and silently drops row 0's OTHER cells (found on a web206-shaped
    row: a non-colon, confidence-0.5 header like "Senior research fellow"
    whose row 1 date range vanished when misrouted this way). #886 recovers
    row 0's other cells directly, below, via `split_merged_cells_in_row`
    (the same primitive the per-row walk uses for an ordinary content row),
    never by routing row 0 through that walk.

    Returns:
        (new_elements, unified_idx, num_table_headers_emitted, table_rows,
         current_content_rows)
    """
    data = table_data.get("data", [])
    row_0 = data[0] if data else []
    row0_is_form_label = (
        first_cell_text.rstrip().endswith(":")
        and row_has_nonblank_value_cells(row_0)
    )

    new_elements: list[dict[str, Any]] = []
    num_table_headers_emitted = 0
    lines = first_cell_text.strip().split('\n')

    if not row0_is_form_label:
        # Use just the first line as the header text
        header_text = lines[0].strip()

        # Emit table header as paragraph-like element
        new_elements.append(
            _table_header_element(unified_idx, header_text, num_tables, header_score, "TableHeader")
        )
        unified_idx += 1
        num_table_headers_emitted += 1

        # Scan remaining rows for sub-headers
        # Some tables have multiple sections with headers in first cell of rows
        table_rows = data[1:]
    else:
        # Row 0 is a form label, not a table header -- let it flow
        # through the per-row walk below like any other row, so it
        # is recovered as content (#811 round 2).
        table_rows = data

    current_content_rows: list[list[dict[str, Any]]] = []

    # IMPORTANT: Check if row 0's first cell has content AFTER the header line
    # This captures cases where a header line is followed by actual content
    # in the same cell (e.g., "K. EXTRAMURAL...\nAssociation of Pediatric...")
    # Only applies when row 0 was actually emitted as the table header above.
    row0_other_cells_recovered = False
    if not row0_is_form_label and len(lines) > 1:
        remaining_content = '\n'.join(lines[1:]).strip()
        # Only treat as content if there's substantial text (multiple lines or >50 chars)
        # This avoids treating single-line noise as content
        if remaining_content and (len(remaining_content) > 50 or remaining_content.count('\n') >= 2):
            # Get the rest of row 0 (other cells) to pair with the remaining content
            if len(row_0) > 1:
                # Create a modified row with the remaining content in cell 0
                modified_row = [{"text": remaining_content}] + row_0[1:]
                current_content_rows.append(modified_row)
                row0_other_cells_recovered = True
            else:
                # Single-cell row - just use the remaining content
                current_content_rows.append([{"text": remaining_content}])

    # #886: a one-row "Role | date-range" table (e.g. "Graduate Research
    # Assistant" | "Aug 2019-May 2022") has a single-line, header-only cell
    # 0 -- the branch above never fires -- yet row 0's OTHER cell(s) still
    # carry a genuine, distinct value that the header text alone does not
    # capture. Recover it the same way the per-row walk recovers a
    # header-left/content-right row at index >= 1 (`split_merged_cells_in_row`
    # on the row as-is), guarded by the same `row_has_nonblank_value_cells`
    # predicate that already distinguishes a real value from a blank or
    # gridSpan-duplicated trailing cell -- so a genuine header-only row 0
    # (blank or duplicate trailing cells) is untouched. Skipped when the
    # branch above already recovered row 0's other cells, to avoid emitting
    # the same value twice.
    if not row0_is_form_label and not row0_other_cells_recovered and row_has_nonblank_value_cells(row_0):
        current_content_rows.extend(split_merged_cells_in_row(row_0))

    return new_elements, unified_idx, num_table_headers_emitted, table_rows, current_content_rows


def row_cell_texts(row: list) -> list[str]:
    """Text of each cell in a table row (cell dicts or bare values)."""
    return [cell.get("text", "") if isinstance(cell, dict) else str(cell) for cell in row]


def join_row_cells(cells: list[str]) -> str:
    """Join a table row's cell texts into one entry text (#488).

    Cells are joined with " | ". When cell 0 holds several paragraphs (an entry
    title followed by sub-bullets), the trailing columns (date, institution)
    describe the whole entry, so they attach to cell 0's FIRST paragraph rather
    than welding onto its last one. A single-paragraph cell 0, or a one-cell
    row, joins exactly as before -- and so does a row where another cell also
    spans lines: the trailing paragraphs could then no longer be told apart
    from those cells' own lines, and stage 6 (honors) reads that old shape.
    """
    first = cells[0] if cells else ""
    rest = cells[1:]
    stripped = first.strip()
    if not rest or "\n" not in stripped or any("\n" in c.strip() for c in rest):
        return " | ".join(cells).strip()
    head, _, tail = first.lstrip().partition("\n")
    return (" | ".join([head, *rest]) + "\n" + tail).strip()


def _flatten_table_content_text(rows: list[Any]) -> str:
    """The ONE join that builds a table_content/table element's `text` from
    its `data` rows: each row via `join_row_cells`, rows joined by "\\n".
    Every construction site in `extract_unified_elements` uses it, as does
    the pre-LLM scrub when it REBUILDS `text` after mutating cells inside
    `data` in place, so `text` (what `extract_text_from_docx` and
    `get_element_text` read) and `data` (what every cell-level consumer
    reads) never disagree (#847 residual round 4). Stage 2 builds its
    per-row entries with the same `join_row_cells`, which is what lets the
    #418 whole-table-parent dedup find the parent's lines in those rows."""
    return "\n".join(
        join_row_cells(row_cell_texts(row)) for row in rows if isinstance(row, list)
    )


def _scrub_pre_llm_pii_row(row: list[Any]) -> None:
    """Round 2 (#847): a table row split into a label cell ("Date of
    Birth:") and a separate value cell ("01/02/1970") has no
    `_PII_FRAGMENT_SPLIT_RE` delimiter between them for
    `redact_pre_llm_values` to extend across -- they are different cells,
    not the same string. After every cell's OWN text is scrubbed in place,
    walk the row once more: a cell that is nothing but a bare DOB/SSN label
    has its value, if any, scrubbed out of the NEXT cell in the same row."""
    for i, cell in enumerate(row):
        if not isinstance(cell, dict):
            continue
        category = pre_llm_bare_label_category(cell.get("text"))
        if category is None or i + 1 >= len(row):
            continue
        nxt = row[i + 1]
        if isinstance(nxt, dict) and isinstance(nxt.get("text"), str):
            nxt["text"] = redact_pre_llm_value_of_category(nxt["text"], category)


def _row_already_resolved(row: list[Any], col_idx: int) -> bool:
    """True if some OTHER cell in `row` already carries
    `PRE_LLM_PLACEHOLDER` -- meaning this row's own same-row scrub
    (`_scrub_pre_llm_pii_row`, or a value sitting in the label's own cell)
    already found and withheld a value beside the label, so the row BELOW
    is an unrelated field, not this label's value (#847 residual round 4:
    a "Date of Birth:" | "01/02/1970" row directly above an "Appointed
    2001" | ... row must not touch "2001" -- the DOB was already resolved
    same-row)."""
    for idx, cell in enumerate(row):
        if idx == col_idx or not isinstance(cell, dict):
            continue
        cell_text = cell.get("text")
        if isinstance(cell_text, str) and PRE_LLM_PLACEHOLDER in cell_text:
            return True
    return False


def _cell_at_grid_col(row: list[Any], grid_col: int) -> dict[str, Any] | None:
    """The cell of `row` covering layout column `grid_col`, or None. Cells
    carry `grid_col`/`grid_span` from `extract_table_metadata`; a cell dict
    without them is one column wide at its list position."""
    for idx, cell in enumerate(row):
        if not isinstance(cell, dict):
            continue
        start = cell.get("grid_col", idx)
        if start <= grid_col < start + cell.get("grid_span", 1):
            return cell
    return None


def _scrub_cells_below(
    next_row: list[Any], label: dict[str, Any], col_idx: int, category: str
) -> None:
    """Scrub each distinct cell of `next_row` overlapping any layout column
    the label cell covers (a merged label spans several)."""
    start = label.get("grid_col", col_idx)
    seen: set[int] = set()
    for grid_col in range(start, start + label.get("grid_span", 1)):
        below = _cell_at_grid_col(next_row, grid_col)
        if not isinstance(below, dict) or id(below) in seen:
            continue
        seen.add(id(below))
        if isinstance(below.get("text"), str):
            below["text"] = redact_pre_llm_value_of_category(
                below["text"], category, cross_boundary=True
            )


def _scrub_pre_llm_pii_column(rows: list[Any]) -> None:
    """Round 3 (#847 residual): a two-row FORM table -- a label cell
    ("Date of Birth") with its value directly below it in the SAME COLUMN
    of the next row, not the next cell of the same row -- has no
    `_PII_FRAGMENT_SPLIT_RE` delimiter and no same-row neighbour for
    `_scrub_pre_llm_pii_row` to see. After every cell's own text is
    scrubbed and every same-row label/value pair is handled, walk row
    pairs: a cell that is nothing but a bare DOB/SSN label, with no value
    ALREADY resolved beside it in its own row (`_row_already_resolved`),
    has its value, if any, scrubbed out of the cell directly BELOW it
    (same layout column, every column a merged label spans) in the next row. `cross_boundary=True`: a cell one
    row down is a lower-confidence position than the same row, so its
    value must open that cell and be a whole date -- "Appointed
    07/01/2005" or "Date of Appointment: 07/01/2005" below a blank "Date
    of Birth:" keeps its date, and so does a bare year."""
    for row_idx in range(len(rows) - 1):
        row, next_row = rows[row_idx], rows[row_idx + 1]
        if not isinstance(row, list) or not isinstance(next_row, list):
            continue
        for col_idx, cell in enumerate(row):
            if not isinstance(cell, dict):
                continue
            category = pre_llm_bare_label_category(cell.get("text"))
            if category is None or _row_already_resolved(row, col_idx):
                continue
            _scrub_cells_below(next_row, cell, col_idx, category)


def _scrub_pre_llm_value_of_category_in_element(el: dict[str, Any], category: str) -> None:
    """Replace the value for `category` that OPENS `el`, wherever `el` keeps
    its text: a plain paragraph's own `text`, or -- for a table element --
    its first cell, with `text` (the pre-flattened join built at
    construction time) rebuilt afterwards from `data`
    (`_flatten_table_content_text`) so the two fields never disagree.
    `cross_boundary=True`: the next element is a lower-confidence position
    than the same cell or row, so only a whole date that opens it counts --
    "Date of Appointment: 07/01/2005", "Appointed Assistant Professor
    07/01/2005" and "1990-1994 BA, Example College" after a blank "Date of
    Birth:" are left alone, and so is every later cell of a table."""
    data = el.get("data")
    if data:
        first_row = data[0] if isinstance(data[0], list) else []
        first = first_row[0] if first_row else None
        if isinstance(first, dict) and isinstance(first.get("text"), str) and first["text"]:
            first["text"] = redact_pre_llm_value_of_category(
                first["text"], category, cross_boundary=True
            )
            el["text"] = _flatten_table_content_text(data)
        return
    text = el.get("text")
    if isinstance(text, str) and text:
        el["text"] = redact_pre_llm_value_of_category(text, category, cross_boundary=True)


def _scrub_pre_llm_pii_next_element(elements: list[dict[str, Any]]) -> None:
    """Round 3 (#847 residual): a label-only PARAGRAPH ("Date of Birth:")
    with its value in the NEXT element of the unified stream -- a separate
    paragraph or table, not the same string `redact_pre_llm_values`'s
    in-text extension can search within. After every element's own text
    is scrubbed, walk the stream once more: an element whose whole text is
    nothing but a bare DOB/SSN label has its value, if any, scrubbed out
    of the very next element (see `_scrub_pre_llm_value_of_category_in_element`
    for a table next-element, whose value can be in `data`, not `text`)."""
    for idx in range(len(elements) - 1):
        text = elements[idx].get("text")
        if not isinstance(text, str):
            continue
        category = pre_llm_bare_label_category(text)
        if category is None:
            continue
        _scrub_pre_llm_value_of_category_in_element(elements[idx + 1], category)


def _scrub_pre_llm_pii_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace DOB/SSN VALUES in place, in every element and table cell
    `extract_unified_elements` built, before any LLM stage reads them
    (#847) -- the single choke point stage 1a, 1b and stage 2 all read
    through. Element count and order are untouched; only a value span's
    text changes. See `redact_pre_llm_values` for what is and is not
    replaced, `_scrub_pre_llm_pii_row` for the same-row label-cell/
    value-cell case, `_scrub_pre_llm_pii_column` for the value directly
    below a label in a two-row form table, and
    `_scrub_pre_llm_pii_next_element` for a label-only paragraph whose
    value is the following paragraph or table.

    A table element's `data` (per-cell) is the source of truth once any
    cell-level scrub runs on it: `text` (the pre-flattened join built at
    construction time) is REBUILT from `data` afterwards
    (`_flatten_table_content_text`), never scrubbed independently, so the
    two can no longer drift apart (#847 residual round 4)."""
    for el in elements:
        data = el.get("data") or []
        if data:
            for row in data:
                if not isinstance(row, list):
                    continue
                for cell in row:
                    if isinstance(cell, dict):
                        cell_text = cell.get("text")
                        if isinstance(cell_text, str) and cell_text:
                            cell["text"] = redact_pre_llm_values(cell_text)
                _scrub_pre_llm_pii_row(row)
            _scrub_pre_llm_pii_column(data)
            el["text"] = _flatten_table_content_text(data)
        else:
            text = el.get("text")
            if isinstance(text, str) and text:
                el["text"] = redact_pre_llm_values(text)
    _scrub_pre_llm_pii_next_element(elements)
    return elements


def _build_whole_table_element(
    table_data: dict[str, Any], unified_idx: int, num_tables: int
) -> dict[str, Any]:
    """The single `table` element for a table with no header-like first cell.

    Every row goes through `split_merged_cells_in_row` first, so a merged cell
    that needs splitting becomes several rows. A row 0 flagged as a column
    header (#424, `extract_table_metadata`) is the exception: it stays one row
    at index 0 exactly as read, so the index stage 2 skips is the header, and
    the element carries `"header_row": True`.
    """
    header_row = table_data.get("header_row", False)
    processed_rows: list[list[dict[str, Any]]] = []
    for row_idx, row in enumerate(table_data["data"]):
        if header_row and row_idx == 0:
            processed_rows.append(row)
        else:
            processed_rows.extend(split_merged_cells_in_row(row))

    element = {
        "unified_idx": unified_idx,
        "type": "table",
        "table_index": num_tables,
        "text": _flatten_table_content_text(processed_rows),
        "rows": len(processed_rows),
        "cols": table_data["cols"],
        "data": processed_rows,
    }
    if header_row:
        element["header_row"] = True
    return element


def extract_unified_elements(docx_path: str) -> dict[str, Any]:
    """
    Extract document elements with table-awareness for header detection.

    This function creates a unified element stream where table headers
    are treated like paragraphs, enabling the header detection logic
    to work on tables too.

    Key differences from extract_docx_structure():
    - Uses unified_idx for all elements (sequential, no gaps)
    - Splits tables into table_header + table_content when first cell is header-like
    - Preserves document order for proper section boundary computation

    Returns:
    {
        "doc_path": str,
        "elements": [
            {"unified_idx": 0, "type": "paragraph", "text": "...", ...},
            {"unified_idx": 1, "type": "table_header", "text": "PUBLICATIONS", "table_index": 0, ...},
            {"unified_idx": 2, "type": "table_content", "table_index": 0, "rows": [...], ...},
            ...
        ],
        "meta": {
            "num_elements": int,
            "num_paragraphs": int,
            "num_tables": int,
            "num_table_headers": int,
            "num_empty": int
        }
    }
    """
    doc = Document(docx_path)

    elements = []
    unified_idx = 0
    num_paragraphs = 0
    num_tables = 0
    num_table_headers = 0
    num_empty = 0

    # A doc.paragraphs position: body paragraphs only. Layout-table cell
    # paragraphs below carry para_idx/idx None -- use unified_idx (#609).
    para_idx = 0

    # Iterate through document body elements in order
    for element in doc.element.body:
        if isinstance(element, CT_P):
            # Paragraph
            para = Paragraph(element, doc)
            text = get_paragraph_text(para).strip()

            if not text:
                # Empty paragraph
                elements.append({
                    "unified_idx": unified_idx,
                    "para_idx": para_idx,
                    "type": "empty",
                    "text": "",
                    "is_empty": True
                })
                num_empty += 1
            else:
                # Non-empty paragraph
                para_data = extract_paragraph_metadata(para, para_idx)
                para_data["unified_idx"] = unified_idx
                para_data["para_idx"] = para_idx
                elements.append(para_data)
                num_paragraphs += 1

            unified_idx += 1
            para_idx += 1

        elif isinstance(element, CT_Tbl):
            # Table
            table = Table(element, doc)

            # Single-column tables are LAYOUT boxes, not tabular data: python-docx's
            # cell.text fuses every paragraph in the cell with '\n', collapsing a whole
            # section into one mega-entry (#208 -- e.g. 89HQVQ's 3716-char TEACHING cell
            # buried 35 paragraphs). Explode the cell paragraphs into individual paragraph
            # elements so header detection and stage-2 splitting see them. Multi-column rows
            # are real data (Year | Institution | Degree) and keep the joined path below.
            # A table nested in the cell is exploded the same way, in document order (#1231).
            if _is_layout_box(table):
                seen_cells = set()
                for row in table.rows:
                    cell = row.cells[0]
                    if cell._tc in seen_cells:   # vertical merge repeats one cell across rows
                        continue
                    seen_cells.add(cell._tc)
                    for cell_para in _iter_cell_paragraphs(cell):
                        if not get_paragraph_text(cell_para).strip():
                            continue
                        para_data = extract_paragraph_metadata(cell_para, None)
                        para_data["unified_idx"] = unified_idx
                        para_data["para_idx"] = None
                        elements.append(para_data)
                        num_paragraphs += 1
                        unified_idx += 1
                num_tables += 1
                continue

            table_data = extract_table_metadata(table, f"table_{num_tables}")
            table_data["table_index"] = num_tables

            # Check if first cell looks like a section header
            first_cell_text = get_table_first_cell_text(table)
            header_score = score_section_header(first_cell_text)

            if header_score.is_header and first_cell_text:
                (
                    row0_elements,
                    unified_idx,
                    row0_num_headers,
                    table_rows,
                    current_content_rows,
                ) = _handle_table_row_zero(
                    table_data, first_cell_text, header_score, unified_idx, num_tables
                )
                elements.extend(row0_elements)
                num_table_headers += row0_num_headers

                for row_idx, row in enumerate(table_rows):
                    # Check if first cell of this row is a sub-header
                    if row and isinstance(row, list) and len(row) > 0:
                        first_cell = row[0]
                        cell_text = first_cell.get("text", "") if isinstance(first_cell, dict) else str(first_cell)
                        cell_text = cell_text.strip()

                        # Check for embedded headers separated by \n\n within a cell
                        # This handles cases like "Research text...\n\nEducation and Degrees\n2005..."
                        if '\n\n' in cell_text and not row_has_nonblank_value_cells(row):  # #259: multi-column rows keep cells 1+
                            segments = cell_text.split('\n\n')

                            # FIRST PASS: Check if ANY segment is a header
                            # Only process segments if we find embedded headers
                            has_any_header = False
                            for segment in segments:
                                segment = segment.strip()
                                if not segment:
                                    continue
                                first_line = segment.split('\n')[0].strip()
                                is_seg_header, _ = looks_like_section_header(first_line)
                                if is_seg_header and len(first_line) <= 80:
                                    has_any_header = True
                                    break

                            # SECOND PASS: Only create synthetic rows if we found headers
                            # Otherwise, preserve the original multi-cell row for split_merged_row_into_pseudo_rows
                            if has_any_header:
                                for seg_idx, segment in enumerate(segments):
                                    segment = segment.strip()
                                    if not segment:
                                        continue

                                    # Check first line of segment for header
                                    first_line = segment.split('\n')[0].strip()
                                    seg_score = score_section_header(first_line)

                                    if seg_score.is_header and len(first_line) <= 80:
                                        # Emit accumulated content first
                                        if current_content_rows:
                                            content_text = _flatten_table_content_text(current_content_rows)
                                            elements.append({
                                                "unified_idx": unified_idx,
                                                "type": "table_content",
                                                "table_index": num_tables,
                                                "text": content_text,
                                                "rows": len(current_content_rows),
                                                "cols": table_data["cols"],
                                                "data": current_content_rows,
                                            })
                                            unified_idx += 1
                                            current_content_rows = []

                                        # Emit the embedded header
                                        elements.append(_table_header_element(
                                            unified_idx, first_line, num_tables, seg_score, "EmbeddedHeader"
                                        ))
                                        unified_idx += 1
                                        num_table_headers += 1

                                        # Remaining lines after header become content
                                        remaining_lines = segment.split('\n')[1:]
                                        if remaining_lines:
                                            remaining_text = '\n'.join(remaining_lines).strip()
                                            if remaining_text:
                                                # Create a synthetic row for the remaining content
                                                synthetic_row = [{"text": remaining_text}]
                                                current_content_rows.append(synthetic_row)
                                    else:
                                        # Not a header segment - add as content
                                        synthetic_row = [{"text": segment}]
                                        current_content_rows.append(synthetic_row)

                                continue  # Skip normal row processing (we handled this row)

                        # Skip if cell text is too long - definitely content, not header
                        # (headers in row cells should be short, not paragraphs)
                        if len(cell_text) > 80:
                            # Check for merged cells that need splitting
                            split_rows = split_merged_cells_in_row(row)
                            current_content_rows.extend(split_rows)
                            continue

                        row_score = score_section_header(cell_text)
                        # A real section header with distinct content on the right (no
                        # colon, high confidence) keeps its header AND recovers the
                        # content, instead of being demoted to content-only (#811 round 2,
                        # web064: "WORK ADDRESS | <address>").
                        has_value_cells, header_left_content_right = _classify_subheader_row_content(
                            cell_text, row_score.confidence, row
                        )

                        # A non-blank trailing cell means "Name:" is a form label, not a
                        # header (#811 round 1) -- UNLESS the row is header-left/content-right
                        # (#811 round 2), in which case the header signal is kept too.
                        if row_score.is_header and cell_text and (not has_value_cells or header_left_content_right):
                            # Emit accumulated content rows first
                            if current_content_rows:
                                content_text = _flatten_table_content_text(current_content_rows)
                                elements.append({
                                    "unified_idx": unified_idx,
                                    "type": "table_content",
                                    "table_index": num_tables,
                                    "text": content_text,
                                    "rows": len(current_content_rows),
                                    "cols": table_data["cols"],
                                    "data": current_content_rows,
                                })
                                unified_idx += 1
                                current_content_rows = []

                            # Emit this row's header
                            sub_header_text = cell_text.split('\n')[0].strip()
                            elements.append(_table_header_element(
                                unified_idx, sub_header_text, num_tables, row_score, "TableSubHeader"
                            ))
                            unified_idx += 1
                            num_table_headers += 1

                            if header_left_content_right:
                                # Keep the header AND recover this row's right-hand content.
                                split_rows = split_merged_cells_in_row(row)
                                current_content_rows.extend(split_rows)
                        else:
                            # Regular content row - check for merged cells that need splitting
                            split_rows = split_merged_cells_in_row(row)
                            current_content_rows.extend(split_rows)
                    else:
                        # Empty or malformed row - also check for merged cells
                        if row:
                            split_rows = split_merged_cells_in_row(row)
                            current_content_rows.extend(split_rows)

                # Emit any remaining content rows
                if current_content_rows:
                    content_text = _flatten_table_content_text(current_content_rows)
                    elements.append({
                        "unified_idx": unified_idx,
                        "type": "table_content",
                        "table_index": num_tables,
                        "text": content_text,
                        "rows": len(current_content_rows),
                        "cols": table_data["cols"],
                        "data": current_content_rows,
                    })
                    unified_idx += 1
            else:
                # No header detected - emit entire table as single element
                elements.append(_build_whole_table_element(table_data, unified_idx, num_tables))
                unified_idx += 1

            num_tables += 1

    return {
        "doc_path": str(docx_path),
        "elements": _scrub_pre_llm_pii_elements(elements),
        "meta": {
            "num_elements": len(elements),
            "num_paragraphs": num_paragraphs,
            "num_tables": num_tables,
            "num_table_headers": num_table_headers,
            "num_empty": num_empty
        }
    }


def extract_docx_structure(docx_path: str) -> dict[str, Any]:
    """
    Extract complete document structure from .docx file.

    IMPORTANT: Element indices match doc.paragraphs indices for consistency
    with Stage 2 entry extraction. Empty paragraphs are included but marked
    as empty (is_empty=True) so they can be skipped in LLM processing.

    Returns:
    {
        "doc_path": str,
        "elements": [
            {"type": "paragraph", "text": "...", "style": "Heading 1", ...},
            {"type": "table", "rows": 3, "cols": 2, "data": [...], ...},
            {"type": "empty", "idx": N},  # Empty paragraphs preserved for index alignment
            ...
        ],
        "meta": {
            "num_elements": int,
            "num_paragraphs": int,
            "num_tables": int,
            "num_empty": int
        }
    }
    """
    doc = Document(docx_path)

    elements = []
    idx = 0
    num_paragraphs = 0
    num_tables = 0
    num_empty = 0

    # Track paragraph index separately (for doc.paragraphs alignment)
    # Tables don't appear in doc.paragraphs, so we need to track carefully
    para_idx = 0

    # Iterate through document body elements in order
    for element in doc.element.body:
        if isinstance(element, CT_P):
            # Paragraph
            para = Paragraph(element, doc)
            text = get_paragraph_text(para).strip()

            if not text:
                # Include empty paragraphs with minimal metadata for index alignment
                # This ensures element indices match doc.paragraphs indices
                elements.append({
                    "idx": para_idx,
                    "type": "empty",
                    "text": "",
                    "is_empty": True
                })
                num_empty += 1
            else:
                para_data = extract_paragraph_metadata(para, para_idx)
                elements.append(para_data)
                num_paragraphs += 1

            para_idx += 1

        elif isinstance(element, CT_Tbl):
            # Table - note: tables don't increment para_idx because they're
            # not in doc.paragraphs. They get their own tracking.
            table = Table(element, doc)
            table_data = extract_table_metadata(table, f"table_{idx}")
            # Mark with special index to distinguish from paragraph indices
            table_data["table_index"] = num_tables
            elements.append(table_data)
            num_tables += 1

        idx += 1

    return {
        "doc_path": str(docx_path),
        "elements": elements,
        "meta": {
            "num_elements": len(elements),
            "num_paragraphs": num_paragraphs,
            "num_tables": num_tables,
            "num_empty": num_empty
        }
    }


class OwnerSideChannel(TypedDict):
    """Text that lives outside the main body-element stream, for stage 4's
    owner-name fallback to consult when the body yielded no name (#456).

    Never merged into `extract_unified_elements`/`extract_docx_structure`'s
    `elements` list and never touches `unified_idx`/`para_idx` -- see
    `extract_owner_side_channel`'s docstring for why.
    """

    sdt_lines: list[str]
    header_lines: list[str]
    footer_lines: list[str]


def _side_channel_paragraph_text(p_elem: CT_P) -> str:
    """Text of a raw `<w:p>` lxml element, same tag walk as `get_paragraph_text`
    (w:t / w:br / w:cr -> newline / w:tab -> space / w:noBreakHyphen -> '-'),
    but operating directly on the element rather than a python-docx
    `Paragraph` wrapper -- sdt-nested paragraphs have no such wrapper
    (python-docx has no `w:sdt` API; see the module-level note in
    `get_paragraph_text`'s docstring)."""
    from docx.oxml.ns import qn

    wt, wbr, wcr, wtab = qn('w:t'), qn('w:br'), qn('w:cr'), qn('w:tab')
    wnbh = qn('w:noBreakHyphen')
    parts = []
    for node in p_elem.iter(wt, wbr, wcr, wtab, wnbh):
        if node.tag == wt:
            if node.text:
                parts.append(node.text)
        elif node.tag == wtab:
            parts.append(' ')
        elif node.tag == wnbh:
            parts.append('-')
        else:  # w:br / w:cr -> line break
            parts.append('\n')
    return ''.join(parts)


def _is_page_field_only_paragraph(para: Paragraph, text: str) -> bool:
    """True if `text` (already `get_paragraph_text(para)`, stripped) is wholly
    accounted for by a PAGE/NUMPAGES field -- the header/footer text a
    side-channel consumer must skip, since it is pagination chrome, not
    letterhead identity/contact content."""
    from docx.oxml.ns import qn

    w_instr_text, w_fld_simple = qn('w:instrText'), qn('w:fldSimple')
    instr_parts = [node.text or '' for node in para._p.iter(w_instr_text)]
    instr_parts += [node.get(qn('w:instr'), '') or '' for node in para._p.iter(w_fld_simple)]
    instr = ' '.join(instr_parts).upper()
    if 'PAGE' not in instr and 'NUMPAGES' not in instr:
        return False
    residual = re.sub(r'[0-9]+', '', text)
    residual = re.sub(r'(?i)\bpage\b|\bof\b', '', residual).strip()
    return len(residual) <= 2


def _header_footer_paragraph_lines(containers: list[Any]) -> list[str]:
    """Non-empty paragraph texts from a list of `_Header`/`_Footer` containers
    (the default/first-page/even-page header or footer proxies for one or
    more sections), in order, deduped by underlying OPC part name.

    A section whose `is_linked_to_previous` is True has no definition of its
    own -- it inherits the previous section's part -- so it is skipped before
    any paragraph access; touching `.paragraphs`/`.part` on a linked proxy
    would call python-docx's `_get_or_add_definition()`, which *adds* a new
    part for a linked first-page/even-page header/footer that has never been
    given one. Checking `is_linked_to_previous` itself never mutates.

    The remaining containers are deduped by `str(container.part.partname)` --
    a stable OPC part name -- never by `id()` of an lxml element proxy or a
    python-docx wrapper: those proxies are created fresh on every attribute
    access and freed the moment they go out of scope, so `id()` collides
    across genuinely distinct elements and misses genuine duplicates
    depending on allocator/GC state (#456 verifier finding 1)."""
    lines: list[str] = []
    seen_partnames: set[str] = set()
    for container in containers:
        if container is None or container.is_linked_to_previous:
            continue
        partname = str(container.part.partname)
        if partname in seen_partnames:
            continue
        seen_partnames.add(partname)
        for para in container.paragraphs:
            text = get_paragraph_text(para).strip()
            if not text:
                continue
            if _is_page_field_only_paragraph(para, text):
                continue
            lines.append(text)
    return lines


def extract_owner_side_channel(docx_path: str) -> OwnerSideChannel:
    """Read CV-owner-identifying text from parts the main body walk never
    opens (#456): a body-level `w:sdt` (Word content-control) wrapping whole
    paragraphs, and the letterhead in `word/header*.xml` / `word/footer*.xml`.

    `extract_unified_elements`/`extract_docx_structure` above dispatch only
    on `isinstance(element, CT_P)` / `isinstance(element, CT_Tbl)` while
    walking `doc.element.body`'s direct children -- a `w:sdt` element matches
    neither, so python-docx has no wrapper for it and its entire subtree
    (including any `w:p` inside) is silently skipped. Neither function opens
    a header/footer OPC part at all.

    This is an ADDITIVE, separate read. It never touches `elements`,
    `unified_idx`, or `para_idx`, and its output is never merged into the
    element stream those two functions return: `doc.paragraphs` (used as a
    fallback index by `stage_2_entry_extraction.py:1118/1153/1226/1245`)
    only enumerates direct-body `CT_P` children, so inlining an sdt paragraph
    into the stream would shift every subsequent `para_idx` and silently
    desync that fallback -- exactly the corruption the #456 issue's comment
    warns against. Consumed only by stage 4's owner-name fallback tier
    (`stage4/owner_name.py`), and only when the body-derived pass found no
    name.

    Args:
        docx_path: Path to the .docx file. Opens it independently -- shares
            no state with `extract_unified_elements`/`extract_docx_structure`.

    Returns:
        OwnerSideChannel: three lists of non-empty paragraph texts, each in
        document order.
        - sdt_lines: every `w:p` anywhere under a `w:sdtContent` in
          `doc.element.body` -- body-level content controls AND ones nested
          inside table cells. `body.iter(w:p)` yields each `w:p` node exactly
          once in document order by construction (it is a single depth-first
          walk of the live tree), so no dedup set is needed or used here --
          an `id()`-keyed `seen` set over lxml element proxies was tried and
          removed: proxies are recreated and freed as they're touched, and a
          freed proxy's address can be reused by a later, genuinely distinct
          element, so `id()` equality is not element identity (#456 verifier
          finding 1 -- a 40-paragraph sdt was dropping to 35 lines).
        - header_lines / footer_lines: paragraphs from every section's
          default, first-page, and even-page header/footer, skipping any
          section proxy that inherits its definition from a previous section
          (`is_linked_to_previous`) and any paragraph whose only content is a
          PAGE/NUMPAGES field (see `_is_page_field_only_paragraph` and
          `_header_footer_paragraph_lines`).
    """
    from docx.oxml.ns import qn

    doc = Document(docx_path)

    w_p, w_sdt_content = qn('w:p'), qn('w:sdtContent')

    sdt_lines: list[str] = []
    for p_elem in doc.element.body.iter(w_p):
        if next(p_elem.iterancestors(w_sdt_content), None) is None:
            continue
        text = _side_channel_paragraph_text(p_elem).strip()
        if text:
            sdt_lines.append(text)

    header_containers: list[Any] = []
    footer_containers: list[Any] = []
    for section in doc.sections:
        header_containers.extend([section.header, section.first_page_header, section.even_page_header])
        footer_containers.extend([section.footer, section.first_page_footer, section.even_page_footer])
    header_lines = _header_footer_paragraph_lines(header_containers)
    footer_lines = _header_footer_paragraph_lines(footer_containers)

    # #847 round 2: this side channel feeds stage4/owner_name.py's LLM
    # fallback tier directly (`_run_owner_name_llm`) -- an sdt-wrapped or
    # letterhead Personal Data block reached that prompt unscrubbed.
    return OwnerSideChannel(
        sdt_lines=_scrub_pre_llm_side_channel_lines(sdt_lines),
        header_lines=_scrub_pre_llm_side_channel_lines(header_lines),
        footer_lines=_scrub_pre_llm_side_channel_lines(footer_lines),
    )


def _scrub_pre_llm_side_channel_lines(lines: list[str]) -> list[str]:
    """`redact_pre_llm_values` on every line, then the body stream's
    next-element rule (`_scrub_pre_llm_pii_next_element`) across lines: a
    line that is nothing but a bare DOB/SSN label has the whole date that
    OPENS the next line withheld (#847 residual: a body-level content
    control with "Date of Birth:" in one paragraph and its value in the
    next, each scrubbed alone, reached the owner-name prompt). The same
    `cross_boundary=True` narrowing: "Appointed 07/01/2005" or a bare year
    on the next line is left alone."""
    scrubbed = [redact_pre_llm_values(line) for line in lines]
    for idx in range(len(scrubbed) - 1):
        category = pre_llm_bare_label_category(scrubbed[idx])
        if category is not None:
            scrubbed[idx + 1] = redact_pre_llm_value_of_category(
                scrubbed[idx + 1], category, cross_boundary=True
            )
    return scrubbed


def normalize_style_name(style_name: str) -> dict[str, Any]:
    """
    Normalize style name to generic role.

    Maps style names to:
    - role: "heading", "normal", "list", "custom"
    - level: 1-6 for headings
    """
    style_lower = style_name.lower()

    # Heading detection
    if 'heading' in style_lower or 'title' in style_lower:
        # Extract level
        level = None
        for i in range(1, 10):
            if str(i) in style_name:
                level = i
                break

        return {
            "role": "heading",
            "level": level or 1,
            "original": style_name
        }

    # List styles
    if 'list' in style_lower or 'bullet' in style_lower:
        return {
            "role": "list",
            "original": style_name
        }

    # Normal/body text
    if style_name in ['Normal', 'Body Text', 'Default']:
        return {
            "role": "normal",
            "original": style_name
        }

    # Custom styles
    return {
        "role": "custom",
        "original": style_name
    }


def create_simplified_layout_json(structure: dict[str, Any], skip_empty: bool = True) -> list[dict[str, Any]]:
    """
    Create simplified layout JSON optimized for LLM processing.

    Reduces verbosity while keeping essential structure cues.

    Args:
        structure: Full document structure from extract_docx_structure
        skip_empty: If True, skip empty paragraphs to reduce LLM token usage.
                   Set to False if you need to preserve all indices.
    """
    simplified = []

    for elem in structure["elements"]:
        # Skip empty paragraphs to save LLM tokens (they have no content to process)
        if elem.get("type") == "empty" or elem.get("is_empty"):
            if skip_empty:
                continue
            # If not skipping, include minimal placeholder
            simplified.append({
                "idx": elem["idx"],
                "type": "empty"
            })
            continue

        if elem["type"] == "paragraph":
            # Simplify paragraph
            style_info = normalize_style_name(elem["style"])

            simplified_elem = {
                "idx": elem["idx"],
                "text": elem["text"],
                "role": style_info["role"]
            }

            # Add level for headings
            if style_info["role"] == "heading" and style_info.get("level"):
                simplified_elem["level"] = style_info["level"]

            # Add list info if present
            if elem["list_level"] is not None:
                simplified_elem["list_level"] = elem["list_level"]

            # Add formatting hints if significant
            if elem["bold"]:
                simplified_elem["bold"] = True
            if elem["indent_left"] > 0.3:  # Significant indentation
                simplified_elem["indent"] = round(elem["indent_left"], 2)

            simplified.append(simplified_elem)

        elif elem["type"] == "table":
            # Keep table structure but simplified
            simplified.append({
                "idx": elem["idx"],
                "type": "table",
                "rows": elem["rows"],
                "cols": elem["cols"],
                "preview": elem["data"][0] if elem["data"] else []
            })

        else:
            # extract_docx_structure emits only paragraph/table/empty. Another
            # type (e.g. extract_unified_elements' table_header/table_content)
            # would otherwise vanish from the layout silently (#614).
            raise ValueError(f"create_simplified_layout_json: unhandled element type {elem['type']!r}")

    return simplified


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python docx_structure_extractor.py <docx_path>")
        sys.exit(1)

    docx_path = sys.argv[1]

    if not Path(docx_path).exists():
        print(f"Error: File not found: {docx_path}")
        sys.exit(1)

    print("Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)

    # Save full structure
    output_path = Path(docx_path).stem + "_structure.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)
    print(f"✓ Full structure saved to: {output_path}")

    # Save simplified layout
    simplified = create_simplified_layout_json(structure)
    simplified_path = Path(docx_path).stem + "_layout.json"
    with open(simplified_path, 'w', encoding='utf-8') as f:
        json.dump(simplified, f, indent=2, ensure_ascii=False)
    print(f"✓ Simplified layout saved to: {simplified_path}")

    print(f"\nSummary:")
    print(f"  Elements: {structure['meta']['num_elements']}")
    print(f"  Paragraphs: {structure['meta']['num_paragraphs']}")
    print(f"  Tables: {structure['meta']['num_tables']}")


if __name__ == '__main__':
    main()
