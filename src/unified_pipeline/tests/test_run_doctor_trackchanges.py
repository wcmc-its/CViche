"""Guard: the doctor's docx readers must see tracked-change INSERTIONS (#249).

Stage 6 inserts LLM-enriched content (research summaries, reformatted citations)
as tracked changes (``<w:ins>``). python-docx's ``.text`` skips those runs, so
the old readers under-reported what rendered and false-flagged content as
'unrendered' (classified_unrendered, unrendered_records, dead_sections). The
readers now read every ``w:t`` under an element — including inside ``<w:ins>``,
excluding deleted ``<w:delText>`` — i.e. the accepted-changes view a reader sees.

    python3 -m pytest src/unified_pipeline/tests/test_run_doctor_trackchanges.py -p no:cacheprovider

Self-contained: no DB, no template. Requires only python-docx.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from unified_pipeline.run_doctor import _docx_text


def test_docx_text_includes_insertions_excludes_deletions():
    p = parse_xml(
        f'<w:p {nsdecls("w")}>'
        '<w:r><w:t>plain </w:t></w:r>'
        '<w:ins><w:r><w:t>inserted</w:t></w:r></w:ins>'
        '<w:del><w:r><w:delText> deleted</w:delText></w:r></w:del>'
        '</w:p>'
    )
    # inserted text is present (was invisible before); deleted text is not
    assert _docx_text(p) == "plain inserted"


def test_docx_text_empty_paragraph():
    p = parse_xml(f'<w:p {nsdecls("w")}></w:p>')
    assert _docx_text(p) == ""


if __name__ == "__main__":
    test_docx_text_includes_insertions_excludes_deletions()
    test_docx_text_empty_paragraph()
    print("OK")
