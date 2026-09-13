"""
Word Document Structure Extractor

Extracts rich layout metadata from .docx files without converting to images.
Leverages Word's native structure (styles, lists, numbering, tables) to create
a lightweight "layout JSON" that can be fed to a text model for segmentation.

This is significantly cheaper and faster than vision-based approaches.
"""

import json
from pathlib import Path
from typing import Any
from docx import Document
from docx.shared import RGBColor, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph


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


def get_paragraph_text(para: Paragraph, tab_char: str = ' ') -> str:
    """Extract all text from a paragraph, including nested structures.

    python-docx's Paragraph.text only concatenates text from direct w:r/w:t children.
    This function also captures text inside:
    - w:smartTag (legacy Word feature for auto-recognizing addresses, names, dates)
    - w:hyperlink
    - w:sdt (structured document tags / content controls)
    - w:ins (tracked-change insertions) -- see #557

    Without this, paragraphs using smartTags appear empty even though they contain text.
    """
    from docx.oxml.ns import qn

    # Walk w:t (text), w:br/w:cr (line breaks) and w:tab in document order so the
    # returned text keeps the paragraph's internal line structure. Bare w:t iteration
    # mashed multi-line paragraphs into run-on text ("CURRICULUM VITAEZachary..."),
    # which hid sub-headers from the chunk LLM once stage 1a converged onto this reader.
    # w:tab -> tab_char (space by default) on purpose: a literal tab would trip the
    # mega-entry record heuristic downstream. iter() also descends into
    # smartTag/hyperlink/sdt/ins.
    WT, WBR, WCR, WTAB = qn('w:t'), qn('w:br'), qn('w:cr'), qn('w:tab')
    parts = []
    for node in para._p.iter(WT, WBR, WCR, WTAB):
        if node.tag == WT:
            if node.text:
                parts.append(node.text)
        elif node.tag == WTAB:
            parts.append(tab_char)
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


def extract_paragraph_metadata(para: Paragraph, idx: int) -> dict[str, Any]:
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
                            padded_lines = lines + [''] * (target_count - len(lines))
                            cell_splits.append(padded_lines[:target_count])
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
                    if split_idx < len(cell_lines_local):
                        new_row.append({"text": cell_lines_local[split_idx]})
                    else:
                        new_row.append({"text": ""})
                elif split_idx == 0:
                    new_row.append(cell)
                else:
                    # Don't duplicate non-split cells - often labels that shouldn't repeat
                    new_row.append({"text": ""})

        split_rows.append(new_row)

    return split_rows


def extract_table_metadata(table: Table, idx: int) -> dict[str, Any]:
    """
    Extract table structure and content.

    Returns table as array of rows with cell metadata.
    """
    rows_data = []

    for row_idx, row in enumerate(table.rows):
        cells_data = []
        # python-docx repeats a horizontally merged (gridSpan) cell once per
        # spanned column, so row.cells returns the SAME _tc object at each
        # spanned col_idx. Without this dedup, a merged single-cell header row
        # like "GRANTS" (spanning 3 columns) arrives here as three identical
        # cells, which the per-row walk misreads as a label|value form row and
        # demotes to content ("GRANTS | GRANTS | GRANTS") -- #811 round 2.
        # Same idiom as the vertical-merge dedup below (~line 660), scoped to
        # one row instead of the whole table.
        seen_tcs_in_row = set()
        for col_idx, cell in enumerate(row.cells):
            if cell._tc in seen_tcs_in_row:
                continue
            seen_tcs_in_row.add(cell._tc)

            # FIX: cell.text sometimes returns empty string for cells with complex formatting
            # or malformed XML (e.g., <w:rPr> inside <w:t> instead of as sibling)
            cell_text = get_cell_text(cell).strip()

            # Fallback: Extract text directly from XML using recursive text extraction
            if not cell_text and cell._element is not None:
                try:
                    # Method 1: Try itertext() which gets ALL text nodes recursively
                    cell_text = ''.join(cell._element.itertext()).strip()
                except:
                    try:
                        # Method 2: Fallback to manual iteration
                        cell_text = ''.join(node.text for node in cell._element.iter() if node.text).strip()
                    except:
                        pass  # If both methods fail, keep empty string

            # Get cell formatting if available
            cell_data = {
                "row": row_idx,
                "col": col_idx,
                "text": cell_text
            }

            cells_data.append(cell_data)

        rows_data.append(cells_data)

    return {
        "idx": idx,
        "type": "table",
        "rows": len(table.rows),
        "cols": len(table.columns),
        "data": rows_data
    }


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


def looks_like_section_header(text: str) -> tuple[bool, float]:
    """
    Determine if text looks like a CV section header.

    Returns:
        (is_header, confidence) tuple where confidence is 0.0-1.0
    """
    if not text:
        return False, 0.0

    # For multi-line text, check just the first line
    # (table cells often have header on first line, content below)
    first_line = text.strip().split('\n')[0].strip()
    text_clean = first_line
    text_lower = text_clean.lower()

    # Too long to be a header (headers are typically short)
    if len(text_clean) > 100:
        return False, 0.0

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
            return False, 0.0

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
            return False, 0.0

    confidence = 0.0

    # Check for known CV section keywords
    for keyword in CV_SECTION_KEYWORDS:
        if keyword in text_lower:
            confidence += 0.4
            break

    # ALL CAPS is a strong header signal
    if text_clean.isupper() and len(text_clean) > 3:
        confidence += 0.3

    # Title Case with short text
    words = text_clean.split()
    if len(words) <= 6 and all(w[0].isupper() for w in words if w):
        confidence += 0.2

    # Ends with colon (common header pattern)
    if text_clean.endswith(':'):
        confidence += 0.1

    # Short text (1-5 words) is more likely a header
    if 1 <= len(words) <= 5:
        confidence += 0.1

    # Very short (1-2 words) known keyword is very likely header
    if len(words) <= 2 and any(kw in text_lower for kw in CV_SECTION_KEYWORDS):
        confidence += 0.2

    # Cap at 1.0
    confidence = min(confidence, 1.0)

    # Threshold for considering it a header
    is_header = confidence >= 0.4

    return is_header, confidence


def row_has_nonblank_value_cells(row: list[dict[str, Any]]) -> bool:
    """Return True if any cell after row[0] carries non-blank text.

    Distinguishes a form-style label|value row (e.g. "Name:" | "<value>")
    from a genuine sub-header row: a single-cell row, or a multi-cell row
    whose trailing cells are all blank, has nothing to lose by being
    emitted as a header (see issue #811).
    """
    for cell in row[1:]:
        cell_value = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        if cell_value.strip():
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


def row_has_distinct_nonblank_value_cells(row: list[dict[str, Any]], label_text: str) -> bool:
    """Return True if any cell after row[0] has non-blank text that DIFFERS
    (case-insensitively) from the row's own label text.

    Defense in depth for the header-left/content-right path (#811 round 2):
    guards against a horizontally merged (gridSpan) cell whose duplicate
    copies repeat the label text itself rather than carrying real content,
    on top of the dedup already applied in extract_table_metadata.
    """
    label_norm = label_text.strip().casefold()
    for cell in row[1:]:
        cell_value = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        cell_value = cell_value.strip()
        if cell_value and cell_value.casefold() != label_norm:
            return True
    return False


def is_header_left_content_right_row(
    cell_text: str, header_confidence: float, row: list[dict[str, Any]]
) -> bool:
    """Return True when a row's first cell is a real section header (not a
    colon-terminated form label) that also carries distinct non-blank content
    in a trailing cell -- the "header-left / content-right" table layout
    (#811 round 2, web064: `WORK ADDRESS | <address>`).

    Such a row keeps its `table_header` AND has its content recovered into
    `current_content_rows`, unlike a form-style label|value row (#811 round 1,
    e.g. `Name: | <value>`), which is content-only.
    """
    if cell_text.rstrip().endswith(":"):
        return False
    if header_confidence < HEADER_LEFT_CONTENT_RIGHT_MIN_CONFIDENCE:
        return False
    return row_has_distinct_nonblank_value_cells(row, cell_text)


def get_table_first_cell_text(table: Table) -> str:
    """Extract text from the first cell of first row of a table."""
    if not table.rows:
        return ""
    first_row = table.rows[0]
    if not first_row.cells:
        return ""
    first_cell = first_row.cells[0]

    cell_text = get_cell_text(first_cell).strip()

    # Fallback extraction if needed
    if not cell_text and first_cell._element is not None:
        try:
            cell_text = ''.join(first_cell._element.itertext()).strip()
        except:
            pass

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

    # Also track original para_idx for backward compatibility
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
            # ponytail: direct cell paragraphs only; a nested table inside a cell (rare) still blobs via cell.text.
            if table.rows and all(len(row.cells) == 1 for row in table.rows):
                seen_cells = set()
                for row in table.rows:
                    cell = row.cells[0]
                    if cell._tc in seen_cells:   # vertical merge repeats one cell across rows
                        continue
                    seen_cells.add(cell._tc)
                    for cell_para in cell.paragraphs:
                        if not get_paragraph_text(cell_para).strip():
                            continue
                        para_data = extract_paragraph_metadata(cell_para, para_idx)
                        para_data["unified_idx"] = unified_idx
                        para_data["para_idx"] = para_idx
                        elements.append(para_data)
                        num_paragraphs += 1
                        unified_idx += 1
                        para_idx += 1
                num_tables += 1
                continue

            table_data = extract_table_metadata(table, f"table_{num_tables}")
            table_data["table_index"] = num_tables

            # Check if first cell looks like a section header
            first_cell_text = get_table_first_cell_text(table)
            is_header, header_confidence = looks_like_section_header(first_cell_text)

            row_0 = table_data["data"][0] if table_data["data"] else []
            # The table's OWN row 0 can be a form-style label|value row too (#811
            # round 2, web207 "NAME: | <value>"), not a real table header. Scoped
            # to the literal form-label shape (colon-terminated label, non-blank
            # trailing cell) -- deliberately NOT reusing the header-left/content-
            # right escape (is_header_left_content_right_row) here: routing a
            # non-colon row 0 into the per-row walk below also exposes it to that
            # walk's separate `\n\n`-embedded-header splitter, which builds
            # single-cell synthetic rows and silently drops row 0's OTHER cells
            # (found on web206's "Assistant Professor of Instruction" -- a real,
            # non-colon, confidence-0.5 header whose row 1 date range vanished
            # when misrouted this way). A row 0 header-left/content-right layout
            # (e.g. "CURRENT POSITION" | <address>) is out of this ticket's scope
            # and keeps today's existing table-level header behavior.
            row0_is_form_label = (
                bool(is_header and first_cell_text)
                and first_cell_text.rstrip().endswith(":")
                and row_has_nonblank_value_cells(row_0)
            )

            if is_header and first_cell_text:
                lines = first_cell_text.strip().split('\n')

                if not row0_is_form_label:
                    # Use just the first line as the header text
                    header_text = lines[0].strip()

                    # Emit table header as paragraph-like element
                    elements.append({
                        "unified_idx": unified_idx,
                        "type": "table_header",
                        "text": header_text,
                        "table_index": num_tables,
                        "header_confidence": header_confidence,
                        "is_header_candidate": True,
                        # Add some paragraph-like metadata for header detection
                        "bold": True,  # Assume table headers are bold-like
                        "style": "TableHeader",
                        "alignment": None,
                        "font_size": None,
                    })
                    unified_idx += 1
                    num_table_headers += 1

                    # Scan remaining rows for sub-headers
                    # Some tables have multiple sections with headers in first cell of rows
                    table_rows = table_data["data"][1:] if table_data["data"] else []
                else:
                    # Row 0 is a form label, not a table header -- let it flow
                    # through the per-row walk below like any other row, so it
                    # is recovered as content (#811 round 2).
                    table_rows = table_data["data"] if table_data["data"] else []

                current_content_rows = []

                # IMPORTANT: Check if row 0's first cell has content AFTER the header line
                # This captures cases where a header line is followed by actual content
                # in the same cell (e.g., "K. EXTRAMURAL...\nAssociation of Pediatric...")
                # Only applies when row 0 was actually emitted as the table header above.
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
                        else:
                            # Single-cell row - just use the remaining content
                            current_content_rows.append([{"text": remaining_content}])

                for row_idx, row in enumerate(table_rows):
                    # Check if first cell of this row is a sub-header
                    if row and isinstance(row, list) and len(row) > 0:
                        first_cell = row[0]
                        cell_text = first_cell.get("text", "") if isinstance(first_cell, dict) else str(first_cell)
                        cell_text = cell_text.strip()

                        # Check for embedded headers separated by \n\n within a cell
                        # This handles cases like "Research text...\n\nEducation and Degrees\n2005..."
                        if '\n\n' in cell_text:
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
                                    is_seg_header, seg_header_conf = looks_like_section_header(first_line)

                                    if is_seg_header and len(first_line) <= 80:
                                        # Emit accumulated content first
                                        if current_content_rows:
                                            content_text = "\n".join(
                                                " | ".join(
                                                    cell.get("text", "") if isinstance(cell, dict) else str(cell)
                                                    for cell in row_data
                                                ) for row_data in current_content_rows
                                            )
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
                                        elements.append({
                                            "unified_idx": unified_idx,
                                            "type": "table_header",
                                            "text": first_line,
                                            "table_index": num_tables,
                                            "header_confidence": seg_header_conf,
                                            "is_header_candidate": True,
                                            "bold": True,
                                            "style": "EmbeddedHeader",
                                            "alignment": None,
                                            "font_size": None,
                                        })
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

                        is_row_header, row_header_conf = looks_like_section_header(cell_text)
                        has_value_cells = row_has_nonblank_value_cells(row)
                        # A real section header with distinct content on the right (no
                        # colon, high confidence) keeps its header AND recovers the
                        # content, instead of being demoted to content-only (#811 round 2,
                        # web064: "WORK ADDRESS | <address>").
                        header_left_content_right = has_value_cells and is_header_left_content_right_row(
                            cell_text, row_header_conf, row
                        )

                        # A non-blank trailing cell means "Name:" is a form label, not a
                        # header (#811 round 1) -- UNLESS the row is header-left/content-right
                        # (#811 round 2), in which case the header signal is kept too.
                        if is_row_header and cell_text and (not has_value_cells or header_left_content_right):
                            # Emit accumulated content rows first
                            if current_content_rows:
                                content_text = "\n".join(
                                    " | ".join(
                                        cell.get("text", "") if isinstance(cell, dict) else str(cell)
                                        for cell in row_data
                                    ) for row_data in current_content_rows
                                )
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
                            elements.append({
                                "unified_idx": unified_idx,
                                "type": "table_header",
                                "text": sub_header_text,
                                "table_index": num_tables,
                                "header_confidence": row_header_conf,
                                "is_header_candidate": True,
                                "bold": True,
                                "style": "TableSubHeader",
                                "alignment": None,
                                "font_size": None,
                            })
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
                    content_text = "\n".join(
                        " | ".join(
                            cell.get("text", "") if isinstance(cell, dict) else str(cell)
                            for cell in row_data
                        ) for row_data in current_content_rows
                    )
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
                # But first, check for merged cells in any row that need splitting
                processed_rows = []
                for row in table_data["data"]:
                    split_rows = split_merged_cells_in_row(row)
                    processed_rows.extend(split_rows)

                # Rebuild content text from processed rows
                content_text = "\n".join(
                    " | ".join(
                        cell.get("text", "") if isinstance(cell, dict) else str(cell)
                        for cell in row_data
                    ) for row_data in processed_rows
                )
                elements.append({
                    "unified_idx": unified_idx,
                    "type": "table",
                    "table_index": num_tables,
                    "text": content_text,
                    "rows": len(processed_rows),
                    "cols": table_data["cols"],
                    "data": processed_rows,
                })
                unified_idx += 1

            num_tables += 1

    return {
        "doc_path": str(docx_path),
        "elements": elements,
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
    with open(output_path, 'w') as f:
        json.dump(structure, f, indent=2)
    print(f"✓ Full structure saved to: {output_path}")

    # Save simplified layout
    simplified = create_simplified_layout_json(structure)
    simplified_path = Path(docx_path).stem + "_layout.json"
    with open(simplified_path, 'w') as f:
        json.dump(simplified, f, indent=2)
    print(f"✓ Simplified layout saved to: {simplified_path}")

    print(f"\nSummary:")
    print(f"  Elements: {structure['meta']['num_elements']}")
    print(f"  Paragraphs: {structure['meta']['num_paragraphs']}")
    print(f"  Tables: {structure['meta']['num_tables']}")


if __name__ == '__main__':
    main()
