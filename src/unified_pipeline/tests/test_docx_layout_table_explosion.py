"""#208: single-column layout tables must explode into per-paragraph elements;
multi-column (real data) tables must NOT. Deterministic, no LLM."""
import sys
from pathlib import Path
_SRC = Path(__file__).resolve().parents[1]  # src/unified_pipeline
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from docx import Document  # noqa: E402
from core.docx_structure_extractor import extract_unified_elements  # noqa: E402


def _build_docx(path):
    doc = Document()
    doc.add_paragraph("TEACHING ACTIVITIES")
    # 1-cell LAYOUT box fusing 3 paragraphs (the #208 shape)
    t = doc.add_table(rows=1, cols=1)
    cell = t.rows[0].cells[0]
    cell.paragraphs[0].text = "Uniformed Services University"
    cell.add_paragraph("Workshop on assessment methods")
    cell.add_paragraph("Korea University College of Medicine")
    # real 2-column DATA table
    d = doc.add_table(rows=2, cols=2)
    d.rows[0].cells[0].text = "2020"; d.rows[0].cells[1].text = "PhD"
    d.rows[1].cells[0].text = "2015"; d.rows[1].cells[1].text = "MS"
    doc.save(path)


def _texts(path):
    els = extract_unified_elements(path)["elements"]
    return [e["text"] for e in els if e.get("text", "").strip()]


def test_layout_table_explodes_but_data_table_stays(tmp_path):
    p = str(tmp_path / "cv.docx")
    _build_docx(p)
    texts = _texts(p)

    # each layout paragraph survives as its OWN line (no fused blob)
    assert "Uniformed Services University" in texts
    assert "Workshop on assessment methods" in texts
    assert "Korea University College of Medicine" in texts
    # nothing fused: no single element holds two of the layout paragraphs
    assert not any("Uniformed" in t and "Korea" in t for t in texts)

    # multi-column data table is NOT exploded -- its cells stay joined together
    assert any("2020" in t and "PhD" in t for t in texts)
    assert any("2015" in t and "MS" in t for t in texts)


if __name__ == "__main__":   # ponytail: runnable without pytest
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "cv.docx")
    _build_docx(p)
    texts = _texts(p)
    assert "Workshop on assessment methods" in texts
    assert not any("Uniformed" in t and "Korea" in t for t in texts)
    assert any("2020" in t and "PhD" in t for t in texts)
    print("OK:", len(texts), "lines ->", texts)
