"""python-docx formatting primitives lifted out of WCMTemplateGenerator (#398).

These nine were methods on the 8,329-line generator class but never touched
`self` -- they were free functions by behaviour and methods only by where they
happened to be written. Moving them changes no logic: the bodies are unmodified
and only the `self` parameter is gone.

They are the Infrastructure end of stage 6. Every one takes a python-docx object
and mutates its formatting; none reads CV data, entry taxonomy, or WCM section
state. `_set_font` alone had 49 call sites in the generator, `_clear_table_data`
22 and `_set_cell_vertical_alignment` 11, so keeping them inside the class was
also the single largest source of `self.` noise in the section writers.

Names keep their leading underscore deliberately. Renaming and relocating in the
same change means a failure cannot be attributed to either; the rename is a
separate, mechanical follow-up.
"""
import sys

try:
    from docx.document import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.shared import Inches, Pt, RGBColor
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover - mirrors stage_6_word_template
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)


def _set_font(run, name='Arial', size=11, bold=False, italic=False):
    """Set font properties for a run - always 11pt Arial unless specified."""
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    # Ensure font name applies to complex script and East Asian text as well
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:ascii'), name)
    rFonts.set(qn('w:hAnsi'), name)
    rFonts.set(qn('w:cs'), name)


def _set_table_border(table: Table, color: str = '808080', size: int = 4):
    """Set table borders to 1px (4 eighths of a point), 50% gray."""
    tbl = table._tbl
    # CT_Tbl.tblPr is a OneAndOnlyOne descriptor: it returns the element or
    # raises InvalidXmlError -- it never returns None. (ECMA-376 makes
    # w:tblPr required on w:tbl, so a valid document always has it.) The
    # old `if tbl.tblPr is not None else OxmlElement(...)` ternary and its
    # trailing `if tbl.tblPr is None: tbl.insert(0, tblPr)` were therefore
    # both unreachable. Note there is no get_or_add_tblPr() to reach for --
    # OneAndOnlyOne generates no such accessor.
    tblPr = tbl.tblPr

    tblBorders = OxmlElement('w:tblBorders')
    for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        border = OxmlElement(f'w:{border_name}')
        border.set(qn('w:val'), 'single')
        border.set(qn('w:sz'), str(size))  # 4 = 0.5pt, 8 = 1pt
        border.set(qn('w:color'), color)
        tblBorders.append(border)

    # Remove existing borders and add new ones
    existing = tblPr.find(qn('w:tblBorders'))
    if existing is not None:
        tblPr.remove(existing)
    tblPr.append(tblBorders)


def _set_cell_vertical_alignment(cell, align='center'):
    """Set cell vertical alignment to center (middle)."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    vAlign = OxmlElement('w:vAlign')
    vAlign.set(qn('w:val'), align)
    # Remove existing vAlign
    existing = tcPr.find(qn('w:vAlign'))
    if existing is not None:
        tcPr.remove(existing)
    tcPr.append(vAlign)


def _set_paragraph_spacing(para, before_pt: int = 4, after_pt: int = 4):
    """Set paragraph spacing before and after.

    Args:
        para: Paragraph to modify
        before_pt: Space before in points
        after_pt: Space after in points
    """
    pPr = para._p.get_or_add_pPr()

    # Remove existing spacing element if present
    existing = pPr.find(qn('w:spacing'))
    if existing is not None:
        pPr.remove(existing)

    # Create new spacing element with before and after
    spacing = OxmlElement('w:spacing')
    spacing.set(qn('w:before'), str(before_pt * 20))  # Convert pt to twips (1pt = 20 twips)
    spacing.set(qn('w:after'), str(after_pt * 20))
    pPr.append(spacing)






def _clear_table_data(table: Table, keep_header: bool = True):
    """Remove all data rows from table."""
    if not table:
        return
    start_row = 1 if keep_header else 0
    for i in range(len(table.rows) - 1, start_row - 1, -1):
        table._element.remove(table.rows[i]._element)


def _set_cell_background(cell, color_hex: str):
    """Set cell background/shading color.

    Args:
        cell: The table cell
        color_hex: Hex color string (without #), e.g., "D9D9D9"
    """
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()

    # Remove existing shading if any
    existing_shd = tcPr.find(qn('w:shd'))
    if existing_shd is not None:
        tcPr.remove(existing_shd)

    # Add new shading element
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), color_hex)
    tcPr.append(shd)


def _set_cell_borders(cell, color_hex: str, size: str = "4"):
    """Set cell borders.

    Args:
        cell: The table cell
        color_hex: Hex color string for border color
        size: Border size in eighths of a point (4 = 0.5pt, 8 = 1pt)
    """
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()

    # Remove existing borders if any
    existing_borders = tcPr.find(qn('w:tcBorders'))
    if existing_borders is not None:
        tcPr.remove(existing_borders)

    # Add new borders element
    tcBorders = OxmlElement('w:tcBorders')

    for border_name in ['top', 'left', 'bottom', 'right']:
        border = OxmlElement(f'w:{border_name}')
        border.set(qn('w:val'), 'single')
        border.set(qn('w:sz'), size)
        border.set(qn('w:space'), '0')
        border.set(qn('w:color'), color_hex)
        tcBorders.append(border)

    tcPr.append(tcBorders)


def _set_cell_text(cell, text: str, bold: bool = False):
    """Set cell text with proper Arial 11pt formatting."""
    cell.text = ""  # Clear existing
    if cell.paragraphs:
        para = cell.paragraphs[0]
        run = para.add_run(str(text) if text else "")
        _set_font(run, bold=bold)


#: CViche's voice to the submitter: a one-cell, light-gray, bordered table in
#: Arial whose first line starts "CViche" (#1388). One table, so deleting it
#: removes the whole note. Fill and border are dark enough to survive print,
#: a projector and dark mode (F2F2F2 with a 0.5pt BFBFBF border washed out).
#: Its text is smaller than the CV's 11pt, so it reads as a note beside it.
CVICHE_BOX_PREFIX = "CViche"
CVICHE_BOX_FILL = "E7E6E6"
CVICHE_BOX_BORDER = "808080"
CVICHE_BOX_BORDER_SIZE = "6"  # eighths of a point: 0.75pt
CVICHE_BOX_TITLE_PT = 9
CVICHE_BOX_TITLE_COLOR = RGBColor(0x59, 0x59, 0x59)
CVICHE_BOX_TEXT_PT = 10
#: Cell padding in twips: 6pt top and bottom, 8pt left and right.
CVICHE_BOX_PADDING = {"top": 120, "bottom": 120, "left": 160, "right": 160}
CVICHE_BOX_FULL_WIDTH = "5000"  # fiftieths of a percent: the whole text column
#: Space outside a box, on the paragraphs either side of it (a table has none).
CVICHE_BOX_OUTSIDE_PT = 6
CVICHE_BOX_LINE_AFTER_PT = 3
CVICHE_BOX_TITLE_AFTER_PT = 6
CVICHE_BOX_GROUP_BEFORE_PT = 6
CVICHE_BOX_GROUP_AFTER_PT = 2
CVICHE_BOX_ITEM_INDENT = Inches(0.25)
CVICHE_BOX_BULLET_HANG = Inches(0.15)
#: "Removed:" / "Kept:" start here, and the quote hangs at the second indent.
#: 0.75" rather than 0.5": "Removed:" in 10pt italic Arial is about 0.63".
CVICHE_BOX_PAIR_LABEL_INDENT = Inches(0.5)
CVICHE_BOX_PAIR_TEXT_INDENT = Inches(1.25)
CVICHE_BOX_BULLET = "\u2022\t"


def add_cviche_box(container: Document | _Cell, title: str) -> _Cell:
    """Append a CViche box to *container* (a Document or a cell), the full
    width of the text column; return its cell, whose first paragraph holds
    *title* small, bold, gray and in small caps."""
    table = container.add_table(rows=1, cols=1)
    _set_cviche_box_geometry(table)
    cell = table.cell(0, 0)
    _set_cell_background(cell, CVICHE_BOX_FILL)
    _set_cell_borders(cell, CVICHE_BOX_BORDER, CVICHE_BOX_BORDER_SIZE)
    title_para = cell.paragraphs[0]
    _cviche_box_spacing(title_para, after=CVICHE_BOX_TITLE_AFTER_PT)
    run = title_para.add_run(title)
    _set_font(run, size=CVICHE_BOX_TITLE_PT, bold=True)
    run.font.small_caps = True
    run.font.color.rgb = CVICHE_BOX_TITLE_COLOR
    return cell


def _set_cviche_box_geometry(table: Table) -> None:
    """Full text-column width and the box's cell padding."""
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "pct")
    tbl_w.set(qn("w:w"), CVICHE_BOX_FULL_WIDTH)
    margins = OxmlElement("w:tblCellMar")
    for side, twips in CVICHE_BOX_PADDING.items():
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:w"), str(twips))
        edge.set(qn("w:type"), "dxa")
        margins.append(edge)
    look = tbl_pr.find(qn("w:tblLook"))
    if look is not None:
        look.addprevious(margins)  # schema order: tblCellMar before tblLook
    else:
        tbl_pr.append(margins)
    tc_w = table.cell(0, 0)._tc.get_or_add_tcPr().find(qn("w:tcW"))
    if tc_w is not None:
        tc_w.set(qn("w:type"), "pct")
        tc_w.set(qn("w:w"), CVICHE_BOX_FULL_WIDTH)


def _cviche_box_spacing(para: Paragraph, before: float = 0,
                        after: float = CVICHE_BOX_LINE_AFTER_PT) -> None:
    fmt = para.paragraph_format
    fmt.space_before, fmt.space_after, fmt.line_spacing = Pt(before), Pt(after), 1.0


def _box_run(para: Paragraph, text: str, bold: bool = False, italic: bool = False) -> None:
    _set_font(para.add_run(text), size=CVICHE_BOX_TEXT_PT, bold=bold, italic=italic)


def cviche_box_text(cell: _Cell, text: str) -> Paragraph:
    """A plain line of the box: 10pt."""
    para = cell.add_paragraph()
    _cviche_box_spacing(para)
    _box_run(para, text)
    return para


def cviche_box_group(cell: _Cell, text: str, first: bool) -> Paragraph:
    """A group label: 10pt bold, with space above unless it opens the box."""
    para = cell.add_paragraph()
    _cviche_box_spacing(para, before=0 if first else CVICHE_BOX_GROUP_BEFORE_PT,
                        after=CVICHE_BOX_GROUP_AFTER_PT)
    _box_run(para, text, bold=True)
    para.paragraph_format.keep_with_next = True
    return para


def cviche_box_item(cell: _Cell, text: str) -> Paragraph:
    """A bulleted item, its wrapped lines hanging under its text."""
    para = cell.add_paragraph()
    _cviche_box_spacing(para)
    fmt = para.paragraph_format
    fmt.left_indent = CVICHE_BOX_ITEM_INDENT + CVICHE_BOX_BULLET_HANG
    fmt.first_line_indent = -CVICHE_BOX_BULLET_HANG
    _box_run(para, f"{CVICHE_BOX_BULLET}{text}")
    return para


def cviche_box_pair(cell: _Cell, label: str, text: str, last: bool) -> Paragraph:
    """One line of a "Removed:" / "Kept:" pair: the label in italic, the
    quote at the second indent, its wrapped lines hanging under the opening
    quote mark. Only the last line of a pair has space after it."""
    para = cell.add_paragraph()
    _cviche_box_spacing(para, after=CVICHE_BOX_LINE_AFTER_PT if last else 0)
    fmt = para.paragraph_format
    fmt.left_indent = CVICHE_BOX_PAIR_TEXT_INDENT
    fmt.first_line_indent = CVICHE_BOX_PAIR_LABEL_INDENT - CVICHE_BOX_PAIR_TEXT_INDENT
    fmt.tab_stops.add_tab_stop(CVICHE_BOX_PAIR_TEXT_INDENT)
    if not last:
        fmt.keep_with_next = True
    _box_run(para, f"{label}\t", italic=True)
    _box_run(para, text)
    return para


def space_around_cviche_box(before: Paragraph | None, after: Paragraph | None) -> None:
    """The space outside a box, set on the paragraphs either side of it: a
    table carries none of its own, and a blank paragraph would survive
    deleting the box."""
    if before is not None:
        before.paragraph_format.space_after = Pt(CVICHE_BOX_OUTSIDE_PT)
    if after is not None:
        after.paragraph_format.space_before = Pt(CVICHE_BOX_OUTSIDE_PT)


def is_cviche_box(table: Table) -> bool:
    """Whether *table* is a CViche box: one cell whose first line starts "CViche"."""
    cells = table._tbl.findall(".//" + qn("w:tc"))
    return len(cells) == 1 and table.cell(0, 0).paragraphs[0].text.startswith(CVICHE_BOX_PREFIX)


class DetachedAnchorError(ValueError):
    """`_insert_after` was handed an anchor with no parent.

    The anchor is a cursor a section writer carried across several inserts
    (the heading, then each table it placed, then a spacing paragraph). If a
    helper in between removed that element from the body -- the generator's
    `_add_spacing_paragraph` does exactly that when its own re-insert fails
    -- the cursor now points at nothing, and the next element would have
    nowhere to go. lxml reports this as a `TypeError` about "siblings of the
    root element", which reads as an XML-layer bug; this names what actually
    happened so the caller can decide (#739 review: patents and mentoring
    both hand-rolled the same body splice and swallowed the failure).
    """


def _insert_after(anchor: BaseOxmlElement, element: BaseOxmlElement) -> None:
    """Move `element` to sit immediately after `anchor` in the document body.

    The one body splice for section writers that build document structure
    (a table per mentee, a table per patent, the spacing paragraph between
    them). python-docx's `add_table` and `add_paragraph` append at the END
    of the body, so every such element has to be moved back under its
    heading; lxml's `addnext` detaches `element` from wherever it currently
    sits, so this is a move, never a copy.

    The position is relative to the anchor ELEMENT, not to a paragraph
    index: `Document.paragraphs` indexes shift every time something is
    spliced in above them, and a body-list index goes stale the same way.
    The three hand-rolled `body.insert(body_elements.index(target) + 1, ...)`
    splices this replaced each caught `(ValueError, IndexError)` for exactly
    that reason and then dropped it (#739 review).

    Raises `DetachedAnchorError` when the anchor has no parent -- see that
    class for when this happens. There is no other failure mode: an
    attached anchor always has a following-sibling slot.
    """
    if anchor.getparent() is None:
        raise DetachedAnchorError(
            "insertion anchor is not attached to the document body")
    anchor.addnext(element)
