"""Guard: the docx readers must not drop w:ins/w:smartTag/w:sdt-wrapped runs
mid-word (#557).

python-docx's Paragraph.text / _Cell.text only concatenate w:r elements that
are DIRECT CHILDREN of w:p. When Word splits a word across runs -- routine
for tracked-change insertions -- a run nested inside w:ins is silently
skipped, dropping 1-2 characters mid-word ("Down Syndrome" reads back as
"Down yndrome"). get_paragraph_text/get_cell_text walk the paragraph's XML
directly and must not reproduce that loss.

    python3 -m pytest src/unified_pipeline/tests/test_docx_structure_extractor_tracked_changes.py -p no:cacheprovider

Self-contained: no DB, no template. Requires only python-docx.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml import parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls  # noqa: E402

from unified_pipeline.core.docx_structure_extractor import (  # noqa: E402
    extract_table_metadata,
    get_cell_text,
    get_paragraph_text,
    get_table_first_cell_text,
)


def _splice_tracked_insertion(second_run, inserted_text):
    """Insert a w:ins-wrapped run containing `inserted_text` immediately before
    `second_run`'s underlying <w:r> -- reproducing the shape Word produces when
    a tracked-change edit splits a word across runs."""
    ins = parse_xml(
        f'<w:ins {nsdecls("w")} w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z">'
        f'<w:r><w:t>{inserted_text}</w:t></w:r></w:ins>'
    )
    second_run._r.addprevious(ins)


def _down_syndrome_paragraph(add_run_to):
    """Build 'Down ' + <w:ins>S</w:ins> + 'yndrome' on whatever python-docx
    object exposes .add_run() (a Paragraph or a cell's first paragraph)."""
    add_run_to.add_run("Down ")
    tail_run = add_run_to.add_run("yndrome")
    _splice_tracked_insertion(tail_run, "S")


def test_get_paragraph_text_reads_through_tracked_insertion():
    doc = Document()
    para = doc.add_paragraph()
    _down_syndrome_paragraph(para)

    assert get_paragraph_text(para) == "Down Syndrome"


def test_get_cell_text_reads_through_tracked_insertion():
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    _down_syndrome_paragraph(cell.paragraphs[0])

    assert get_cell_text(cell) == "Down Syndrome"


def test_extract_table_metadata_does_not_mangle_tracked_insertion(tmp_path):
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    _down_syndrome_paragraph(cell.paragraphs[0])

    metadata = extract_table_metadata(table, idx=0)
    cell_text = metadata["data"][0][0]["text"]
    assert cell_text == "Down Syndrome"
    assert "yndrome" not in cell_text.replace("Syndrome", "")


def test_get_table_first_cell_text_does_not_mangle_tracked_insertion():
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    _down_syndrome_paragraph(cell.paragraphs[0])

    assert get_table_first_cell_text(table) == "Down Syndrome"


def test_get_paragraph_text_tab_char_parameterized():
    doc = Document()
    para = doc.add_paragraph()
    para.add_run("a")
    para.add_run().add_tab()
    para.add_run("b")

    assert get_paragraph_text(para) == "a b"
    assert get_paragraph_text(para, tab_char='\t') == "a\tb"
