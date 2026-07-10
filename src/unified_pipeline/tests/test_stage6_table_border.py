"""_set_table_border writes borders onto the table's *attached* w:tblPr.

Guards the removal of a dead `tbl.tblPr is None` guard. CT_Tbl.tblPr is a
OneAndOnlyOne descriptor -- it returns the element or raises InvalidXmlError,
and never returns None -- so the old ternary's `else OxmlElement('w:tblPr')`
branch and its trailing `if tbl.tblPr is None: tbl.insert(0, tblPr)` were both
unreachable. If someone "restores" that pattern, the borders would be written
to a detached element and silently vanish from the saved document; this test
fails in that case.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_table_border.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _border_table():
    doc = docx.Document()
    return doc, doc.add_table(rows=2, cols=2)


def test_borders_land_on_the_attached_tblPr():
    doc, table = _border_table()
    WCMTemplateGenerator._set_table_border(None, table, color="808080", size=4)

    tbl = table._tbl
    tblPr = tbl.find(qn("w:tblPr"))
    assert tblPr is not None, "w:tblPr must be present in the tree"
    assert tbl[0] is tblPr, "w:tblPr must remain the first child (schema order)"

    borders = tblPr.find(qn("w:tblBorders"))
    assert borders is not None, "borders were written to a detached element"

    edges = {b.tag.split("}")[1] for b in borders}
    assert edges == {"top", "left", "bottom", "right", "insideH", "insideV"}
    for b in borders:
        assert b.get(qn("w:val")) == "single"
        assert b.get(qn("w:sz")) == "4"
        assert b.get(qn("w:color")) == "808080"


def test_reapplying_replaces_rather_than_duplicates_borders():
    doc, table = _border_table()
    WCMTemplateGenerator._set_table_border(None, table)
    WCMTemplateGenerator._set_table_border(None, table, color="FF0000", size=8)

    tblPr = table._tbl.find(qn("w:tblPr"))
    all_borders = tblPr.findall(qn("w:tblBorders"))
    assert len(all_borders) == 1, "old w:tblBorders must be removed, not stacked"
    assert all_borders[0][0].get(qn("w:color")) == "FF0000"
