"""Regression test for issue #317: M1 (Research Activities) entries silently
dropped when the Stage 4.5 research summary is absent.

`_fill_research_summary` renders only the Stage 4.5 synthesized summary; the raw
M1 entries are consumed upstream by Stage 4.5. When Stage 4.5 produced nothing,
the summary did not render AND M1 was in `mapped_codes` (excluded from the
appendix), so M1 content vanished (C0ZGFW). The fix makes `_fill_research_summary`
report whether it rendered, and routes M1 entries to the appendix when it did not.

Self-contained: no DB, no network, no PII. Uses the bundled WCM template and
python-docx. The LLM-driven appendix-reconsider pass is neutralized so the test
is deterministic and needs no credentials. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_m1_appendix_fallback.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

M1_TOKEN = "DISTINCTIVE_M1_TOKEN"


def _output_text(docx_path) -> str:
    doc = Document(str(docx_path))
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _render(tmp_path, entries) -> str:
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the routing
    # under test and keeps the render deterministic + credential-free.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TESTAA", "entries": entries}
    ip = tmp_path / "in.json"
    op = tmp_path / "out.docx"
    ip.write_text(json.dumps(data))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return _output_text(op)


def test_m1_entry_rendered_when_no_research_summary(tmp_path):
    """The bug: with no Stage 4.5 summary, the M1 entry rendered nowhere. After
    the fix it falls through to the appendix and its content survives."""
    entries = [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": f"The {M1_TOKEN} research program on widget dynamics ran 2010 to 2014",
         "taxonomy_code": "M1", "extracted_fields": {"narrative": "ran 2010 to 2014"},
         "element_idx_start": 5},
    ]
    text = _render(tmp_path, entries)
    assert M1_TOKEN in text, "M1 entry was dropped when no research summary rendered"


def test_fill_research_summary_reports_whether_it_rendered():
    """The signal the fix depends on: False when nothing renders."""
    gen = WCMTemplateGenerator(verbose=False)
    assert gen._fill_research_summary(None) is False
    assert gen._fill_research_summary({"research_summary": {"text": ""}}) is False
    assert gen._fill_research_summary({"research_summary": {"text": "too short"}}) is False
